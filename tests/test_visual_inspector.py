"""
Visual Bug Inspector (Phase 3) — capture sizing, targeting and task shaping.
Pure logic + local screen grabs; no network.
Run: python -m pytest tests/test_visual_inspector.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import vision_model as vm
import visual_inspector as vi

PIL = pytest.importorskip("PIL")
from PIL import Image


# ── token budget: area, not width ─────────────────────────────────────────────
# Image tokens scale with pixel AREA. Region capture is worth doing precisely
# because a window spends the same budget on far less content, so its text lands
# several times larger than in a downscaled full-desktop grab.

def test_fit_for_send_caps_a_full_desktop_by_area():
    out = vm.fit_for_send(Image.new("RGB", (1920, 1080)))
    assert out.size[0] * out.size[1] <= vm.SEND_MAX_PIXELS


def test_fit_for_send_never_upscales_a_small_window():
    small = Image.new("RGB", (400, 300))          # well under budget
    assert vm.fit_for_send(small).size == (400, 300)


def test_fit_for_send_preserves_aspect_ratio():
    out = vm.fit_for_send(Image.new("RGB", (1600, 900)))
    assert abs((out.size[0] / out.size[1]) - (1600 / 900)) < 0.02


def test_a_window_keeps_more_detail_than_the_same_content_full_screen():
    # 1000x620 terminal: as a cropped window it is barely downscaled; inside a
    # full 1920x1080 grab the same pixels shrink by the desktop's scale factor
    win_scale = vm.fit_for_send(Image.new("RGB", (1000, 620))).size[0] / 1000
    desk_scale = vm.fit_for_send(Image.new("RGB", (1920, 1080))).size[0] / 1920
    assert win_scale > desk_scale * 1.5


# ── capture targeting ─────────────────────────────────────────────────────────

def test_capture_defaults_to_the_active_window():
    shot = vi.capture()
    assert shot["ok"] and shot["scope"] in ("window", "screen")
    assert shot["sent_w"] * shot["sent_h"] <= vm.SEND_MAX_PIXELS


def test_capture_honours_an_explicit_region():
    shot = vi.capture(region=(0, 0, 320, 240))
    assert shot["ok"] and shot["scope"] == "region"
    assert shot["w"] == 320 and shot["h"] == 240


def test_capture_can_be_forced_to_the_whole_screen():
    shot = vi.capture(whole_screen=True)
    assert shot["ok"] and shot["scope"] == "screen"


def test_capture_returns_a_crisp_preview_and_a_smaller_payload():
    shot = vi.capture(whole_screen=True)
    # the preview the user sees must not be the shrunken model payload
    assert len(shot["preview_b64"]) > len(shot["b64"])
    assert Path(shot["path"]).exists()


def test_foreground_window_reports_a_sane_box_or_nothing():
    box, title = vi.foreground_window()
    assert box is None or (box[2] > box[0] and box[3] > box[1])
    assert isinstance(title, str)


# ── the task handed to the coding agent ───────────────────────────────────────

def test_build_task_carries_the_report_and_the_reading():
    task = vi.build_task("Traceback ... gui.py line 3947 KeyError",
                         "the stop button does nothing", "C:/shots/a.jpg")
    assert "the stop button does nothing" in task
    assert "gui.py line 3947" in task
    assert "C:/shots/a.jpg" in task


def test_build_task_tells_the_agent_it_cannot_see_the_image():
    # the coding agent's model is text-only; it must know the reading is its
    # only view, and that the FILES win when the two disagree
    task = vi.build_task("some reading", "some report")
    assert "cannot see the image" in task
    assert "trust the files" in task


def test_build_task_forbids_inventing_a_fix_for_foreign_software():
    task = vi.build_task("a Photoshop crash dialog", "fix this")
    assert "not this project" in task


def test_build_task_survives_an_empty_reading():
    assert vi.build_task("", "") .strip()          # never blank
