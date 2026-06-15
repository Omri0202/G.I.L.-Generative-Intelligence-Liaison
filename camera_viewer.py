"""
camera_viewer.py -- G.I.L. Vision feed + Real-Time Gesture Detection.
Launched via PowerShell Start-Process by eyes.py. Runs standalone -- no tkinter.

Strategy: write FRAME_FILE immediately so GIL's 8-s watcher is satisfied,
then load the MediaPipe model in a background thread so gesture detection
starts a few seconds later without blocking camera startup.

Outputs:
  FRAME_FILE   -- latest JPEG (for eyes.py identify), written every 0.4 s
  GESTURE_FILE -- current gesture state JSON, written every frame
"""

import cv2
import sys
import json
import time
import threading
import tempfile
import ctypes
from pathlib import Path

FRAME_FILE   = Path(tempfile.gettempdir()) / "gil_cam_frame.jpg"
GESTURE_FILE = Path(tempfile.gettempdir()) / "gil_gesture_state.json"
KILL_FILE    = Path(tempfile.gettempdir()) / "gil_cam_kill.txt"
WIN_NAME     = "G.I.L. Vision"
STARTUP_SEC  = 3.0

_MODEL_FILE = Path(__file__).parent / "data" / "hand_landmarker.task"
_MODEL_URL  = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)

# ---- Camera init (FIRST -- so GIL's watcher sees frames immediately) --------

cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
if not cap.isOpened():
    cap = cv2.VideoCapture(0)
if not cap.isOpened():
    print("[G.I.L. VISION] No camera found.")
    sys.exit(1)

cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
cap.set(cv2.CAP_PROP_FPS, 30)

for _ in range(3):
    cap.read()

# Write first frame immediately -- GIL's _watch() waits up to 8 s for this
_ret0, _f0 = cap.read()
if _ret0 and _f0 is not None:
    try:
        cv2.imwrite(str(FRAME_FILE), _f0, [cv2.IMWRITE_JPEG_QUALITY, 88])
    except Exception:
        pass

cv2.namedWindow(WIN_NAME, cv2.WINDOW_NORMAL)
cv2.resizeWindow(WIN_NAME, 640, 480)

_sw = ctypes.windll.user32.GetSystemMetrics(0)
_sh = ctypes.windll.user32.GetSystemMetrics(1)
cv2.moveWindow(WIN_NAME, (_sw - 640) // 2, (_sh - 480) // 2)

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
            num_hands=1,
            min_hand_detection_confidence=0.65,
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

    pinch = _dist2d(lm, 4, 8)

    wrist_y      = lm[0].y
    thumb_up_dir = lm[4].y < wrist_y - 0.12
    thumb_dn_dir = lm[4].y > lm[9].y

    cx, cy  = lm[8].x, lm[8].y
    none_up = not (idx or mid or rng or pnk)

    if pinch < 0.05:
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
    if idx and mid and not rng and not pnk:
        return "peace", cx, cy
    if idx and mid and rng and not pnk:
        return "three_up", cx, cy
    if not idx and not mid and not rng and pnk:
        return "call_me", cx, cy
    return None, cx, cy

# ---- Visual helpers ----------------------------------------------------------

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
}

_HOLD_FRAMES = {
    "peace": 25, "thumbs_up": 18, "thumbs_down": 18,
    "fist": 18, "open_hand": 22, "three_up": 18, "call_me": 18,
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
                cv2.FONT_HERSHEY_SIMPLEX, scale, col,        thick,     cv2.LINE_AA)
    req = _HOLD_FRAMES.get(gesture)
    if req:
        progress = min(hold_count / req, 1.0)
        bx, by, bw, bh = 14, h - 22, w - 28, 7
        cv2.rectangle(frame, (bx, by), (bx + bw, by + bh), (30, 30, 30), -1)
        filled = int(bw * progress)
        if filled > 0:
            bar_col = (0, 255, 0) if progress >= 1.0 else col
            cv2.rectangle(frame, (bx, by), (bx + filled, by + bh), bar_col, -1)

# ---- Main loop --------------------------------------------------------------

start_time        = time.time()
last_frame_save   = 0.0
last_gesture_save = 0.0
_brought_to_front = False
_frame_ts_ms      = 0

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

    display = cv2.flip(raw_frame, 1)
    now     = time.time()

    # ---- Gesture detection (only when model is ready) -----------------------
    gesture_name = None
    gx, gy       = 0.5, 0.5

    with _model_lock:
        _lm_ready = _GESTURE_ENABLED
        _lm       = _landmarker

    if _lm_ready and _lm:
        try:
            import mediapipe as mp
            rgb = cv2.cvtColor(display, cv2.COLOR_BGR2RGB)
            mp_image  = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            _frame_ts_ms += 33
            result    = _lm.detect_for_video(mp_image, _frame_ts_ms)

            if result.hand_landmarks:
                lm = result.hand_landmarks[0]
                _draw_hand(display, lm)
                gesture_name, gx, gy = classify_gesture(lm)

            if gesture_name != _hold_gesture:
                _hold_gesture = gesture_name
                _hold_count   = 0
            else:
                _hold_count = min(_hold_count + 1, 999)

            _draw_gesture_hud(display, gesture_name, _hold_count)

            hand_visible = bool(result.hand_landmarks)
            if hand_visible or (now - last_gesture_save > 0.1):
                try:
                    state = {"gesture": gesture_name, "x": gx, "y": gy, "hand": hand_visible, "ts": now}
                    tmp_g = GESTURE_FILE.with_name("gil_gesture_tmp.json")
                    tmp_g.write_text(json.dumps(state))
                    tmp_g.replace(GESTURE_FILE)
                    last_gesture_save = now
                except Exception:
                    pass
        except Exception as _det_exc:
            print("[G.I.L. VISION] Detection error: " + str(_det_exc))

    # ---- Frame save for eyes.py (unmirrored -- better for LLM) -------------
    if now - last_frame_save > 0.4:
        tmp = FRAME_FILE.with_name("gil_cam_frame_tmp.jpg")
        if cv2.imwrite(str(tmp), raw_frame, [cv2.IMWRITE_JPEG_QUALITY, 88]):
            try:
                tmp.replace(FRAME_FILE)
            except Exception:
                pass
        last_frame_save = now

    # ---- Overlay ------------------------------------------------------------
    h, w = display.shape[:2]
    cv2.rectangle(display, (0, 0), (w, 38), (1, 1, 24), -1)
    if _lm_ready:
        hud = "G.I.L. VISION  |  GESTURE ON"
    else:
        hud = "G.I.L. VISION  |  LOADING MODEL..."
    cv2.putText(display, hud,
                (10, 26), cv2.FONT_HERSHEY_SIMPLEX,
                0.62, (0, 191, 255), 1, cv2.LINE_AA)

    cv2.imshow(WIN_NAME, display)

    key = cv2.waitKey(1)
    if key == 27:
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

for _f in (FRAME_FILE, GESTURE_FILE):
    try:
        _f.unlink(missing_ok=True)
    except Exception:
        pass
