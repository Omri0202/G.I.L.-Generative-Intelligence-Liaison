"""
@file mentioning & context pinning (Feature #6) — pure logic, no GUI/network.
Run: python -m pytest tests/test_file_mentions.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import code_agent as ca


def _tree(root: Path):
    """A miniature project with the noise we must filter out."""
    (root / "main.py").write_text("print('hi')\n", encoding="utf-8")
    (root / "gil_brain.py").write_text("brain\n", encoding="utf-8")
    (root / ".env").write_text("GROQ_API_KEY=secret123\n", encoding="utf-8")
    (root / "notes.log").write_text("noise\n", encoding="utf-8")
    (root / "app.exe").write_bytes(b"\x00binary")
    sub = root / "web"; sub.mkdir()
    (sub / "index.html").write_text("<p>x</p>\n", encoding="utf-8")
    for junk in ("__pycache__", "node_modules", "whatsapp_profile", ".git"):
        d = root / junk; d.mkdir()
        (d / "junk.py").write_text("junk\n", encoding="utf-8")
    ca._file_cache.update(root=None, files=[], ts=0.0)     # bust the walk cache
    return root


# ── the autocomplete corpus ───────────────────────────────────────────────────

def test_lists_source_files(tmp_path):
    _tree(tmp_path)
    got = ca.list_project_files("", root=tmp_path)
    assert "main.py" in got and "web/index.html" in got


def test_never_lists_dotfiles_or_secrets(tmp_path):
    _tree(tmp_path)
    got = ca.list_project_files("", root=tmp_path)
    assert not any(f.split("/")[-1].startswith(".") for f in got)
    assert not any("env" == f for f in got)
    assert ".env" not in got                       # the API key must stay invisible


def test_skips_binary_log_and_noise_dirs(tmp_path):
    _tree(tmp_path)
    got = ca.list_project_files("", root=tmp_path)
    assert "app.exe" not in got and "notes.log" not in got
    assert not any(d in f for f in got
                   for d in ("__pycache__", "node_modules", "whatsapp_profile", ".git"))


def test_ranks_name_matches_first(tmp_path):
    _tree(tmp_path)
    got = ca.list_project_files("brain", root=tmp_path)
    assert got and got[0] == "gil_brain.py"


def test_subsequence_fallback_matches(tmp_path):
    _tree(tmp_path)
    assert "web/index.html" in ca.list_project_files("winx", root=tmp_path)


def test_limit_is_respected(tmp_path):
    for i in range(60):
        (tmp_path / f"f{i}.py").write_text("x\n", encoding="utf-8")
    ca._file_cache.update(root=None, files=[], ts=0.0)
    assert len(ca.list_project_files("", limit=10, root=tmp_path)) == 10


# ── pinned context block ──────────────────────────────────────────────────────

def test_pinned_context_has_header_and_fenced_body(tmp_path):
    (tmp_path / "a.py").write_text("VALUE = 42\n", encoding="utf-8")
    out = ca.read_pinned_context(["a.py"], root=tmp_path)
    assert "PINNED CONTEXT" in out and "### a.py" in out
    assert "```py" in out and "VALUE = 42" in out


def test_pinned_context_refuses_dotfiles(tmp_path):
    (tmp_path / ".env").write_text("GROQ_API_KEY=secret123\n", encoding="utf-8")
    assert ca.read_pinned_context([".env"], root=tmp_path) == ""


def test_pinned_context_truncates_large_file(tmp_path):
    (tmp_path / "big.py").write_text("x" * 50_000, encoding="utf-8")
    out = ca.read_pinned_context(["big.py"], cap_each=500, root=tmp_path)
    assert "[truncated]" in out and len(out) < 2000


def test_pinned_context_respects_total_budget(tmp_path):
    for n in ("a.py", "b.py", "c.py"):
        (tmp_path / n).write_text("y" * 900, encoding="utf-8")
    out = ca.read_pinned_context(["a.py", "b.py", "c.py"],
                                 cap_each=1000, cap_total=1000, root=tmp_path)
    assert "budget reached" in out


def test_pinned_context_empty_and_missing(tmp_path):
    assert ca.read_pinned_context([]) == ""
    assert ca.read_pinned_context([""]) == ""
    out = ca.read_pinned_context(["nope.py"], root=tmp_path)
    assert "could not read" in out
