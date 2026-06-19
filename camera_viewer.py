"""
camera_viewer.py -- G.I.L. Vision feed + Real-Time Gesture Detection.
Launched via PowerShell Start-Process by eyes.py. Runs standalone -- no tkinter.

Window layout: 820 x 480
  Left  640px -- camera feed with hand landmarks, face recognition overlay
  Right 180px -- gesture guide sidebar (current bindings + last result)

Outputs:
  FRAME_FILE   -- latest JPEG (for eyes.py identify), written every 0.4 s
  GESTURE_FILE -- current gesture state JSON, written every frame
  RESULT_FILE  -- last fired action result (written by gestures.py, read here)
"""

import cv2
import sys
import json
import time
import threading
import tempfile
import ctypes
import numpy as np
from pathlib import Path

FRAME_FILE   = Path(tempfile.gettempdir()) / "gil_cam_frame.jpg"
GESTURE_FILE = Path(tempfile.gettempdir()) / "gil_gesture_state.json"
FACE_FILE    = Path(tempfile.gettempdir()) / "gil_face_state.json"
KILL_FILE    = Path(tempfile.gettempdir()) / "gil_cam_kill.txt"
RESULT_FILE  = Path(tempfile.gettempdir()) / "gil_gesture_result.json"

WIN_NAME     = "G.I.L. Vision"
STARTUP_SEC  = 3.0
DISPLAY_W    = 640
DISPLAY_H    = 480
SIDEBAR_W    = 180
WIN_W        = DISPLAY_W + SIDEBAR_W   # 820

_MODEL_FILE = Path(__file__).parent / "data" / "hand_landmarker.task"
_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)
_GESTURE_CONFIG_FILE = Path(__file__).parent / "data" / "gesture_config.json"

# ---- Camera init (FIRST -- so GIL's watcher sees frames immediately) --------

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if not cap.isOpened():
    cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("[G.I.L. VISION] No camera found.")
    sys.exit(1)

cap.set(cv2.CAP_PROP_FRAME_WIDTH,  DISPLAY_W)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, DISPLAY_H)
cap.set(cv2.CAP_PROP_FPS, 30)

for _ in range(3):
    cap.read()

_ret0, _f0 = cap.read()
if _ret0 and _f0 is not None:
    try:
        cv2.imwrite(str(FRAME_FILE), _f0, [cv2.IMWRITE_JPEG_QUALITY, 88])
    except Exception:
        pass

# ---- Window setup -- show first frame immediately to avoid black flash -------

_sw = ctypes.windll.user32.GetSystemMetrics(0)
_sh = ctypes.windll.user32.GetSystemMetrics(1)

cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)
cv2.resizeWindow(WIN_NAME, WIN_W, DISPLAY_H)
cv2.moveWindow(WIN_NAME, (_sw - WIN_W) // 2, (_sh - DISPLAY_H) // 2)

if _ret0 and _f0 is not None:
    _init_canvas = np.zeros((DISPLAY_H, WIN_W, 3), dtype=np.uint8)
    _init_canvas[:, :DISPLAY_W] = cv2.flip(_f0, 1)
    _init_canvas[:, DISPLAY_W:] = (8, 8, 22)
    cv2.putText(_init_canvas, "G.I.L. VISION", (DISPLAY_W + 8, 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0, 191, 255), 1, cv2.LINE_AA)
    cv2.putText(_init_canvas, "Loading model...", (DISPLAY_W + 8, 46),
                cv2.FONT_HERSHEY_SIMPLEX, 0.38, (40, 80, 100), 1, cv2.LINE_AA)
    cv2.imshow(WIN_NAME, _init_canvas)
    cv2.waitKey(1)

# ---- Gesture config (hot-reloaded in main loop) -----------------------------

_GESTURE_CONFIG      = {}
_GESTURE_CONFIG_MTIME = 0.0

_DEFAULT_LABELS = {
    "thumbs_up":   "Vol Up",
    "thumbs_down": "Vol Down",
    "peace":       "Screenshot",
    "fist":        "Mute",
    "open_hand":   "Announce",
    "rock_on":     "DND Mode",
    "three_up":    "Next Track",
    "call_me":     "Prev Track",
    "index_point": "Cursor",
}

def _load_gesture_config():
    global _GESTURE_CONFIG, _GESTURE_CONFIG_MTIME
    try:
        mtime = _GESTURE_CONFIG_FILE.stat().st_mtime
        if mtime <= _GESTURE_CONFIG_MTIME:
            return
        data = json.loads(_GESTURE_CONFIG_FILE.read_text(encoding="utf-8"))
        _GESTURE_CONFIG      = data.get("gestures", {})
        _GESTURE_CONFIG_MTIME = mtime
    except Exception:
        pass

_load_gesture_config()

# ---- MediaPipe model -- loaded in background thread -------------------------

_landmarker      = None
_GESTURE_ENABLED = False
_model_lock      = threading.Lock()

def _load_model():
    global _landmarker, _GESTURE_ENABLED
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as _mp_py
        from mediapipe.tasks.python import vision as _mp_vis

        if not _MODEL_FILE.exists():
            try:
                import urllib.request
                _MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
                print("[G.I.L. VISION] Downloading hand landmarker model (~8 MB)...")
                urllib.request.urlretrieve(_MODEL_URL, _MODEL_FILE)
                print("[G.I.L. VISION] Model downloaded.")
            except Exception as dl_exc:
                print("[G.I.L. VISION] Model download failed: " + str(dl_exc))
                return

        base = _mp_py.BaseOptions(model_asset_path=str(_MODEL_FILE))
        opts = _mp_vis.HandLandmarkerOptions(
            base_options=base,
            num_hands=2,
            min_hand_detection_confidence=0.60,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            running_mode=_mp_vis.RunningMode.VIDEO,
        )
        lm = _mp_vis.HandLandmarker.create_from_options(opts)
        with _model_lock:
            _landmarker      = lm
            _GESTURE_ENABLED = True
        print("[G.I.L. VISION] Hand landmarker ready.")
    except Exception as exc:
        print("[G.I.L. VISION] MediaPipe load failed: " + str(exc))

threading.Thread(target=_load_model, daemon=True, name="GIL-ModelLoad").start()

# ---- Face recognition -- background thread at 2 fps -------------------------

_face_result = {
    "status":        "idle",
    "name":          None,
    "confidence":    0.0,
    "face_box":      None,
    "result_ts":     0.0,
    "result_status": None,
    "result_name":   None,
    "result_box":    None,
}
_face_lock   = threading.Lock()
_raw_lock    = threading.Lock()
_latest_raw  = [None]

def _face_loop():
    try:
        from face_id import FaceID
        fid = FaceID()
        print("[G.I.L. VISION] Face recognition ready.")
    except Exception as exc:
        print("[G.I.L. VISION] Face recognition unavailable: " + str(exc))
        return

    last_result_status = None
    result_ts          = 0.0
    last_face_ts       = 0.0
    no_face_since      = 0.0

    while True:
        time.sleep(0.5)
        with _raw_lock:
            frame = _latest_raw[0]
        if frame is None:
            continue

        with _face_lock:
            _face_result["status"] = "scanning"

        try:
            result = fid.identify(frame)
        except Exception:
            continue

        now = time.time()

        if result["face_box"] is not None:
            last_face_ts  = now
            no_face_since = 0.0
            cur_box       = result["face_box"]
        else:
            if no_face_since == 0.0:
                no_face_since = now
            if now - no_face_since > 2.0:
                result_ts          = 0.0
                last_result_status = None
            cur_box = None

        if result["status"] in ("match", "unknown"):
            if result["status"] != last_result_status:
                result_ts = now
            last_result_status = result["status"]
            result_box  = result["face_box"] or cur_box
            result_name = result["name"]
            result_stat = result["status"]
        else:
            result_box  = _face_result.get("result_box")
            result_name = _face_result.get("result_name")
            result_stat = _face_result.get("result_status")

        with _face_lock:
            _face_result.update({
                "status":        result["status"],
                "name":          result["name"],
                "confidence":    result["confidence"],
                "face_box":      cur_box,
                "result_ts":     result_ts,
                "result_status": result_stat,
                "result_name":   result_name,
                "result_box":    result_box,
            })

        try:
            tmp = FACE_FILE.with_name("gil_face_tmp.json")
            tmp.write_text(json.dumps({
                "name":       result_name,
                "confidence": result["confidence"],
                "status":     result_stat or result["status"],
                "ts":         now,
            }))
            tmp.replace(FACE_FILE)
        except Exception:
            pass

threading.Thread(target=_face_loop, daemon=True, name="GIL-FaceRecog").start()

# ---- Gesture classifier ------------------------------------------------------

def _ext(lm, tip, pip_j):
    return lm[tip].y < lm[pip_j].y

def _dist2d(lm, a, b):
    return ((lm[a].x - lm[b].x) ** 2 + (lm[a].y - lm[b].y) ** 2) ** 0.5

def classify_gesture(lm):
    """lm: list[NormalizedLandmark] (21 items). Returns (name|None, cx, cy)."""
    idx = _ext(lm, 8,  6)
    mid = _ext(lm, 12, 10)
    rng = _ext(lm, 16, 14)
    pnk = _ext(lm, 20, 18)

    pinch    = _dist2d(lm, 4, 8)
    wrist_y  = lm[0].y

    # Thumbs up: all fingers curled, thumb tip clearly above wrist
    thumb_up_dir = lm[4].y < wrist_y - 0.07
    # Thumbs down: all fingers curled, thumb tip clearly below wrist
    thumb_dn_dir = lm[4].y > lm[0].y + 0.04

    cx = lm[5].x
    cy = lm[8].y
    none_up = not (idx or mid or rng or pnk)

    if pinch < 0.11:
        return "pinch", cx, cy
    if none_up and thumb_up_dir:
        return "thumbs_up", cx, cy
    if none_up and thumb_dn_dir:
        return "thumbs_down", cx, cy
    if none_up:
        return "fist", cx, cy
    if idx and mid and rng and pnk:
        return "open_hand", cx, cy
    if idx and not mid and not rng and not pnk:
        return "index_point", cx, cy
    if idx and not mid and not rng and pnk:
        return "rock_on", cx, cy
    if idx and mid and not rng and not pnk:
        return "peace", cx, cy
    if idx and mid and rng and not pnk:
        return "three_up", cx, cy
    if not idx and not mid and not rng and pnk:
        return "call_me", cx, cy
    return None, cx, cy

# ---- Drawing helpers ---------------------------------------------------------

def _stext(img, text, pos, scale, col, thick=1):
    """Shadow text: black outline + colored foreground."""
    cv2.putText(img, text, (pos[0]+1, pos[1]+1),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 1, cv2.LINE_AA)
    cv2.putText(img, text, pos,
                cv2.FONT_HERSHEY_SIMPLEX, scale, col, thick, cv2.LINE_AA)

_GESTURE_LABELS = {
    "index_point": ("CURSOR MODE",  (0,   230, 255)),
    "pinch":       ("CLICK",        (0,   200, 255)),
    "peace":       ("SCREENSHOT",   (80,  255, 80)),
    "thumbs_up":   ("VOL  UP",      (80,  255, 80)),
    "thumbs_down": ("VOL  DOWN",    (80,  255, 80)),
    "fist":        ("MUTE",         (80,  180, 255)),
    "open_hand":   ("OPEN HAND",    (200, 200, 200)),
    "three_up":    ("NEXT TRACK",   (80,  255, 80)),
    "call_me":     ("PREV TRACK",   (80,  255, 80)),
    "rock_on":     ("DND TOGGLE",   (255, 100, 200)),
}

_SIDEBAR_ROWS = [
    ("thumbs_up",   "THUMB UP",  (80,  255, 80)),
    ("thumbs_down", "THUMB DN",  (80,  220, 80)),
    ("peace",       "PEACE",     (80,  255, 80)),
    ("fist",        "FIST",      (80,  180, 255)),
    ("open_hand",   "HAND",      (200, 200, 200)),
    ("rock_on",     "ROCK ON",   (255, 100, 200)),
    ("three_up",    "3 UP",      (80,  255, 80)),
    ("call_me",     "CALL ME",   (80,  255, 200)),
    ("index_point", "POINT",     (0,   230, 255)),
]

_HOLD_FRAMES_DISPLAY = {
    "peace": 15, "thumbs_up": 15, "thumbs_down": 15,
    "fist": 15, "open_hand": 18, "three_up": 15, "call_me": 15,
    "rock_on": 20,
}

_HAND_CONNECTIONS = [
    (0,1),(1,2),(2,3),(3,4),
    (0,5),(5,6),(6,7),(7,8),
    (0,9),(9,10),(10,11),(11,12),
    (0,13),(13,14),(14,15),(15,16),
    (0,17),(17,18),(18,19),(19,20),
    (5,9),(9,13),(13,17),
]

def _draw_hand(frame, lm):
    h, w = frame.shape[:2]
    pts  = {i: (int(lm[i].x * w), int(lm[i].y * h)) for i in range(21)}
    for a, b in _HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 210, 210), 2, cv2.LINE_AA)
    for i, pt in pts.items():
        r = 6 if i in (4, 8, 12, 16, 20) else 3
        cv2.circle(frame, pt, r, (255, 255, 255), -1, cv2.LINE_AA)
        cv2.circle(frame, pt, r, (0, 180, 180),   1, cv2.LINE_AA)

def _draw_gesture_hud(frame, gesture, hold_count):
    """Draws the active gesture label + hold-progress bar onto the camera frame."""
    if gesture not in _GESTURE_LABELS:
        return
    h, w         = frame.shape[:2]
    label, col   = _GESTURE_LABELS[gesture]
    scale, thick = 0.72, 2
    (tw, _), _   = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    tx = w - tw - 14
    ty = h - 48
    cv2.putText(frame, label, (tx+1, ty+1),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 1, cv2.LINE_AA)
    cv2.putText(frame, label, (tx, ty),
                cv2.FONT_HERSHEY_SIMPLEX, scale, col, thick, cv2.LINE_AA)
    req = _HOLD_FRAMES_DISPLAY.get(gesture)
    if req:
        progress = min(hold_count / req, 1.0)
        bx, by, bw, bh = 14, h - 22, w - 28, 7
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (30, 30, 30), -1)
        filled = int(bw * progress)
        if filled > 0:
            bar_col = (0, 255, 0) if progress >= 1.0 else col
            cv2.rectangle(frame, (bx, by), (bx + filled, by + bh), bar_col, -1)

def _draw_sidebar(canvas, current_gesture, hold_count, last_result):
    """Draws the gesture guide panel on the right 180px of canvas."""
    x  = DISPLAY_W
    h  = DISPLAY_H
    sw = SIDEBAR_W

    # Background
    canvas[:, x:] = (8, 8, 22)
    # Left border accent
    cv2.line(canvas, (x, 0), (x, h), (0, 60, 80), 1)

    # Header
    _stext(canvas, "GESTURE GUIDE", (x + 6, 20), 0.46, (0, 191, 255))
    cv2.line(canvas, (x, 28), (x + sw, 28), (0, 50, 70), 1)
    _stext(canvas, "hold to activate", (x + 6, 42), 0.33, (30, 60, 80))
    cv2.line(canvas, (x, 48), (x + sw, 48), (0, 30, 50), 1)

    # Gesture rows
    y = 64
    for gkey, gname, row_col in _SIDEBAR_ROWS:
        active = current_gesture == gkey

        # Highlight background for active gesture
        if active:
            hl = tuple(max(0, c // 5) for c in row_col)
            cv2.rectangle(canvas, (x, y - 13), (x + sw, y + 7), hl, -1)

        # Action label from config (live)
        action_label = (
            _GESTURE_CONFIG.get(gkey, {}).get("label")
            or _DEFAULT_LABELS.get(gkey, "—")
        )
        # Truncate to fit sidebar
        action_label = action_label[:12]

        col = row_col if active else (35, 60, 80)
        _stext(canvas, gname,        (x + 6,  y), 0.36, col)
        _stext(canvas, action_label, (x + 86, y), 0.36, col)
        y += 22

    # Hold progress bar for active gesture
    if current_gesture and current_gesture in _HOLD_FRAMES_DISPLAY:
        req  = _HOLD_FRAMES_DISPLAY[current_gesture]
        prog = min(hold_count / req, 1.0)
        bx, bw_bar = x + 4, sw - 8
        by_bar     = y + 2
        cv2.rectangle(canvas, (bx, by_bar), (bx + bw_bar, by_bar + 5), (15, 25, 35), -1)
        filled = int(bw_bar * prog)
        if filled > 0:
            bar_col = (0, 255, 80) if prog >= 1.0 else (0, 160, 255)
            cv2.rectangle(canvas, (bx, by_bar), (bx + filled, by_bar + 5), bar_col, -1)
        y += 14

    y += 6
    cv2.line(canvas, (x, y), (x + sw, y), (0, 30, 50), 1)
    y += 12

    # Two-hand combos
    _stext(canvas, "TWO-HAND COMBOS", (x + 6, y), 0.34, (0, 80, 110))
    y += 18
    for cname, clabel in [
        ("2x FIST",  "Lock Screen"),
        ("2x THUMB", "Max Volume"),
        ("2x HAND",  "Show Desktop"),
    ]:
        _stext(canvas, cname,  (x + 6,  y), 0.33, (30, 55, 75))
        _stext(canvas, clabel, (x + 72, y), 0.33, (30, 55, 75))
        y += 17

    # Last action result (fades over 4 s)
    if last_result:
        res_text, res_ts = last_result
        age   = time.time() - res_ts
        if age < 4.0:
            fade  = 1.0 if age < 2.5 else max(0.0, 1.0 - (age - 2.5) / 1.5)
            rcol  = tuple(int(c * fade) for c in (60, 255, 100))
            cv2.line(canvas, (x, h - 36), (x + sw, h - 36), (0, 40, 20), 1)
            _stext(canvas, "DONE:", (x + 6, h - 20), 0.33, tuple(int(c * 0.6 * fade) for c in (60, 255, 100)))
            _stext(canvas, res_text[:20], (x + 46, h - 20), 0.36, rcol)

    # Settings hint at very bottom
    _stext(canvas, "Settings > Gestures", (x + 4, h - 6), 0.30, (25, 40, 55))

# ---- Face recognition overlay -----------------------------------------------

_RESULT_HOLD = 2.8

def _draw_face_overlay(frame, face_result, tick):
    import math, random

    now           = time.time()
    h, w          = frame.shape[:2]
    status        = face_result.get("status", "idle")
    result_ts     = float(face_result.get("result_ts", 0.0))
    result_status = face_result.get("result_status")
    result_name   = face_result.get("result_name") or ""
    result_box    = face_result.get("result_box")
    scan_box      = face_result.get("face_box")

    in_result_window = (
        result_status in ("match", "unknown")
        and result_ts > 0
        and (now - result_ts) < _RESULT_HOLD
    )
    in_scan = status == "scanning" and scan_box is not None and result_ts == 0.0

    if not in_result_window and not in_scan:
        return

    if in_scan:
        fx, fy, fw, fh = scan_box
        fx = max(0, fx); fy = max(0, fy)
        fw = min(fw, w - fx); fh = min(fh, h - fy)
        if fw < 10 or fh < 10:
            return

        col = (0, 220, 255)
        rng = random.Random(tick // 5)
        for _ in range(12):
            px = fx + rng.randint(4, max(5, fw - 4))
            py = fy + rng.randint(4, max(5, fh - 4))
            a  = rng.random()
            cv2.circle(frame, (px, py), rng.randint(1, 3),
                       tuple(int(c * a) for c in col), -1, cv2.LINE_AA)

        raw_pos  = (tick * 5) % (fh * 2)
        scan_off = raw_pos if raw_pos < fh else fh * 2 - raw_pos
        scan_y   = max(fy, min(fy + fh - 1, fy + int(scan_off)))
        for trail in range(20):
            ty = scan_y - trail
            if fy <= ty <= fy + fh:
                a  = max(0.0, (1.0 - trail / 20.0) ** 1.4)
                tc = tuple(int(c * a) for c in col)
                cv2.line(frame, (fx, ty), (fx + fw, ty), tc, 1)
        cv2.line(frame, (fx, scan_y), (fx + fw, scan_y), (230, 245, 255), 1)

        blen = max(8, int(min(fw, fh) * 0.22 * min(1.0, tick / 20.0)))
        for cx2, cy2, dx1, dy1, dx2, dy2 in [
            (fx,      fy,       1, 0,  0,  1),
            (fx+fw,   fy,      -1, 0,  0,  1),
            (fx,      fy+fh,    1, 0,  0, -1),
            (fx+fw,   fy+fh,   -1, 0,  0, -1),
        ]:
            e1 = (cx2 + dx1*blen, cy2 + dy1*blen)
            e2 = (cx2 + dx2*blen, cy2 + dy2*blen)
            cv2.line(frame, (cx2, cy2), e1, (50, 100, 130), 4, cv2.LINE_AA)
            cv2.line(frame, (cx2, cy2), e2, (50, 100, 130), 4, cv2.LINE_AA)
            cv2.line(frame, (cx2, cy2), e1, col, 2, cv2.LINE_AA)
            cv2.line(frame, (cx2, cy2), e2, col, 2, cv2.LINE_AA)

        cx_c, cy_c = fx + fw // 2, fy + fh // 2
        cv2.line(frame, (cx_c-6, cy_c), (cx_c+6, cy_c), col, 1, cv2.LINE_AA)
        cv2.line(frame, (cx_c, cy_c-6), (cx_c, cy_c+6), col, 1, cv2.LINE_AA)

        dots  = "." * ((tick // 8) % 4)
        atxt  = "ANALYZING" + dots
        (aw, ah), _ = cv2.getTextSize(atxt, cv2.FONT_HERSHEY_SIMPLEX, 0.48, 1)
        ax = fx + (fw - aw) // 2
        ay = fy + fh + ah + 8
        if ay < h - 4:
            cv2.putText(frame, atxt, (ax+1, ay+1),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, (0,0,0), 2, cv2.LINE_AA)
            cv2.putText(frame, atxt, (ax, ay),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.48, col, 1, cv2.LINE_AA)
        return

    if in_result_window and result_box:
        fx, fy, fw, fh = result_box
        fx = max(0, fx); fy = max(0, fy)
        fw = min(fw, w - fx); fh = min(fh, h - fy)
        if fw < 10 or fh < 10:
            return

        elapsed   = now - result_ts
        col       = (70, 255, 110) if result_status == "match" else (40, 60, 255)

        if elapsed < 0.15:
            alpha = elapsed / 0.15
        elif elapsed > _RESULT_HOLD - 0.5:
            alpha = max(0.0, (_RESULT_HOLD - elapsed) / 0.5)
        else:
            alpha = 1.0

        if alpha <= 0:
            return

        if elapsed < 0.15:
            fl = frame.copy()
            cv2.rectangle(fl, (fx, fy), (fx+fw, fy+fh), col, -1)
            cv2.addWeighted(fl, 0.30 * alpha, frame, 1.0 - 0.30 * alpha, 0, frame)

        blen    = max(8, int(min(fw, fh) * 0.22))
        bright  = tuple(min(255, int(c * alpha)) for c in col)
        dim_col = tuple(max(0, int(c * 0.25 * alpha)) for c in col)
        for cx2, cy2, dx1, dy1, dx2, dy2 in [
            (fx,      fy,       1, 0,  0,  1),
            (fx+fw,   fy,      -1, 0,  0,  1),
            (fx,      fy+fh,    1, 0,  0, -1),
            (fx+fw,   fy+fh,   -1, 0,  0, -1),
        ]:
            e1 = (cx2 + dx1*blen, cy2 + dy1*blen)
            e2 = (cx2 + dx2*blen, cy2 + dy2*blen)
            cv2.line(frame, (cx2, cy2), e1, dim_col, 5, cv2.LINE_AA)
            cv2.line(frame, (cx2, cy2), e2, dim_col, 5, cv2.LINE_AA)
            cv2.line(frame, (cx2, cy2), e1, bright, 2, cv2.LINE_AA)
            cv2.line(frame, (cx2, cy2), e2, bright, 2, cv2.LINE_AA)

        rlabel = result_name.upper() if result_status == "match" else "UNIDENTIFIED"
        scale  = 0.82
        thick2 = 2
        (lw, lh2), _ = cv2.getTextSize(rlabel, cv2.FONT_HERSHEY_SIMPLEX, scale, thick2)
        lx = fx + (fw - lw) // 2
        ly = fy - 14
        if ly < lh2 + 6:
            ly = fy + fh + lh2 + 18
        glow = tuple(max(0, int(c * 0.35 * alpha)) for c in col)
        for g in (3, 2):
            cv2.putText(frame, rlabel, (lx-g, ly-g),
                        cv2.FONT_HERSHEY_SIMPLEX, scale, glow, thick2+g*2, cv2.LINE_AA)
        cv2.putText(frame, rlabel, (lx+1, ly+1),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (0,0,0), thick2+1, cv2.LINE_AA)
        cv2.putText(frame, rlabel, (lx, ly),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, bright, thick2, cv2.LINE_AA)

        sub = "IDENTITY CONFIRMED" if result_status == "match" else "IDENTITY UNKNOWN"
        sub_col = tuple(max(0, int(c * 0.65 * alpha)) for c in col)
        (sw_t, _), _ = cv2.getTextSize(sub, cv2.FONT_HERSHEY_SIMPLEX, 0.36, 1)
        cv2.putText(frame, sub, (fx + (fw-sw_t)//2, ly + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, sub_col, 1, cv2.LINE_AA)

# ---- Result file reader ------------------------------------------------------

def _read_result():
    try:
        data = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        return data.get("text", ""), data.get("ts", 0.0)
    except Exception:
        return None

# ---- Main loop ---------------------------------------------------------------

start_time        = time.time()
last_frame_save   = 0.0
last_gesture_save = 0.0
last_config_check = 0.0
_brought_to_front = False
_frame_ts_ms      = 0
_anim_tick        = 0

_hold_gesture = None
_hold_count   = 0

while True:
    if KILL_FILE.exists():
        try:
            KILL_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        break

    ret, raw_frame = cap.read()
    if not ret:
        time.sleep(0.05)
        continue

    display    = cv2.flip(raw_frame, 1)
    now        = time.time()
    _anim_tick += 1

    # Reload gesture config every 5 s
    if now - last_config_check > 5.0:
        _load_gesture_config()
        last_config_check = now

    # Share raw frame with face recognition thread
    with _raw_lock:
        _latest_raw[0] = raw_frame.copy()

    # ---- Gesture detection --------------------------------------------------
    gesture_name  = None
    gesture2_name = None
    gx, gy        = 0.5, 0.5

    with _model_lock:
        _lm_ready = _GESTURE_ENABLED
        _lm       = _landmarker

    if _lm_ready and _lm:
        try:
            import mediapipe as mp
            rgb       = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
            mp_image  = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            _frame_ts_ms = int(now * 1000)
            result    = _lm.detect_for_video(mp_image, _frame_ts_ms)

            if result.hand_landmarks:
                lm = result.hand_landmarks[0]
                _draw_hand(display, lm)
                gesture_name, gx, gy = classify_gesture(lm)

                if len(result.hand_landmarks) > 1:
                    lm2   = result.hand_landmarks[1]
                    h2, w2 = display.shape[:2]
                    pts2  = {i: (int(lm2[i].x * w2), int(lm2[i].y * h2)) for i in range(21)}
                    for a, b in _HAND_CONNECTIONS:
                        cv2.line(display, pts2[a], pts2[b], (30, 180, 255), 2, cv2.LINE_AA)
                    for i, pt in pts2.items():
                        r = 6 if i in (4, 8, 12, 16, 20) else 3
                        cv2.circle(display, pt, r, (255, 255, 255), -1, cv2.LINE_AA)
                        cv2.circle(display, pt, r, (20, 140, 200),   1, cv2.LINE_AA)
                    gesture2_name, _, _ = classify_gesture(lm2)

            if gesture_name != _hold_gesture:
                _hold_gesture = gesture_name
                _hold_count   = 0
            else:
                _hold_count = min(_hold_count + 1, 999)

            _draw_gesture_hud(display, gesture_name, _hold_count)

            # Two-hand combo HUD on camera frame
            if gesture_name and gesture2_name:
                _combo_key = "/".join(sorted([gesture_name, gesture2_name]))
                _combo_labels = {
                    "fist/fist":           ("LOCK SCREEN",  (255,  80,  80)),
                    "thumbs_up/thumbs_up": ("VOL  MAX",     (80,  255,  80)),
                    "open_hand/open_hand": ("SHOW DESKTOP", (200, 200, 200)),
                }
                if _combo_key in _combo_labels:
                    _cl, _cc = _combo_labels[_combo_key]
                    _h, _w   = display.shape[:2]
                    cv2.putText(display, f"  {_cl}", (10, _h - 22),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.62, (0,0,0), 3, cv2.LINE_AA)
                    cv2.putText(display, f"  {_cl}", (10, _h - 22),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.62, _cc, 1, cv2.LINE_AA)

            # Face overlay
            with _face_lock:
                _fr_snap = dict(_face_result)
                if _fr_snap.get("face_box"):
                    _fr_snap["face_box"] = tuple(_fr_snap["face_box"])
            _draw_face_overlay(display, _fr_snap, _anim_tick)

            hand_visible = bool(result.hand_landmarks)
            if hand_visible or (now - last_gesture_save > 0.1):
                try:
                    state = {
                        "gesture":  gesture_name,
                        "gesture2": gesture2_name,
                        "x": gx, "y": gy,
                        "hand": hand_visible,
                        "ts": now,
                    }
                    tmp_g = GESTURE_FILE.with_name("gil_gesture_tmp.json")
                    tmp_g.write_text(json.dumps(state))
                    tmp_g.replace(GESTURE_FILE)
                    last_gesture_save = now
                except Exception:
                    pass
        except Exception as _det_exc:
            print("[G.I.L. VISION] Detection error: " + str(_det_exc))

    # ---- Frame save for eyes.py (unmirrored) --------------------------------
    if now - last_frame_save > 0.4:
        tmp = FRAME_FILE.with_name("gil_cam_frame_tmp.jpg")
        if cv2.imwrite(str(tmp), raw_frame, [cv2.IMWRITE_JPEG_QUALITY, 88]):
            try:
                tmp.replace(FRAME_FILE)
            except Exception:
                pass
        last_frame_save = now

    # ---- Camera header bar --------------------------------------------------
    h_d, w_d = display.shape[:2]
    cv2.rectangle(display, (0, 0), (w_d, 38), (1, 1, 24), -1)
    hud = "G.I.L. VISION  |  GESTURE ON" if _lm_ready else "G.I.L. VISION  |  LOADING..."
    cv2.putText(display, hud, (10, 26), cv2.FONT_HERSHEY_SIMPLEX,
                0.62, (0, 191, 255), 1, cv2.LINE_AA)

    # ---- Compose canvas: camera + sidebar -----------------------------------
    canvas = np.zeros((DISPLAY_H, WIN_W, 3), dtype=np.uint8)
    canvas[:, :DISPLAY_W] = display
    _draw_sidebar(canvas, gesture_name, _hold_count, _read_result())

    cv2.imshow(WIN_NAME, canvas)

    key = cv2.waitKey(1)
    if key == 27:   # Esc
        break

    if not _brought_to_front:
        _brought_to_front = True
        cv2.setWindowProperty(WIN_NAME, cv2.WND_PROP_TOPMOST, 1)
        _hwnd = ctypes.windll.user32.FindWindowW(None, WIN_NAME)
        if _hwnd:
            _u32 = ctypes.windll.user32
            _u32.ShowWindow(_hwnd, 9)
            _u32.SetWindowPos(_hwnd, -1, 0, 0, 0, 0, 0x0003 | 0x0040)
            _u32.BringWindowToTop(_hwnd)
            _u32.SetForegroundWindow(_hwnd)
            _u32.FlashWindow(_hwnd, False)

    if time.time() - start_time > STARTUP_SEC:
        try:
            if cv2.getWindowProperty(WIN_NAME, cv2.WND_PROP_VISIBLE) < 0:
                break
        except Exception:
            break

# ---- Cleanup ----------------------------------------------------------------

cap.release()
cv2.destroyAllWindows()

with _model_lock:
    if _landmarker:
        _landmarker.close()

for _f in (FRAME_FILE, GESTURE_FILE, FACE_FILE):
    try:
        _f.unlink(missing_ok=True)
    except Exception:
        pass
