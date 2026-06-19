"""
face_id.py -- G.I.L. Facial Recognition
Uses InsightFace (ONNX runtime, no TensorFlow) for face detection + recognition.
Model: buffalo_s (~300 MB, downloads automatically on first use).
"""

import json
import threading
import numpy as np
from pathlib import Path

FACE_DB    = Path(__file__).parent / "data" / "face_db"
EMBED_FILE = Path(__file__).parent / "data" / "face_embeddings.json"

# InsightFace ArcFace cosine similarity: same person > ~0.30, different < ~0.15
_SIM_THRESHOLD = 0.25

_app      = None
_app_lock = threading.Lock()


def _get_app():
    global _app
    with _app_lock:
        if _app is None:
            from insightface.app import FaceAnalysis
            a = FaceAnalysis(name="buffalo_s",
                             providers=["CPUExecutionProvider"])
            a.prepare(ctx_id=0, det_size=(320, 320))
            _app = a
    return _app


class FaceID:

    def __init__(self):
        FACE_DB.mkdir(parents=True, exist_ok=True)
        self._embeddings = {}    # name -> list[np.ndarray]
        self._mtime      = 0.0
        self._load()

    # ---- Persistence ---------------------------------------------------------

    def _load(self):
        if not EMBED_FILE.exists():
            return
        try:
            mtime = EMBED_FILE.stat().st_mtime
            if mtime <= self._mtime:
                return
            raw = json.loads(EMBED_FILE.read_text())
            self._embeddings = {
                name: [np.array(e) for e in embeds]
                for name, embeds in raw.items()
            }
            self._mtime = mtime
        except Exception:
            pass

    def _save(self):
        raw = {name: [e.tolist() for e in embeds]
               for name, embeds in self._embeddings.items()}
        tmp = EMBED_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw))
        tmp.replace(EMBED_FILE)

    # ---- Public API ----------------------------------------------------------

    def enroll(self, frame_bgr, name="Omri"):
        """Detect face in frame, extract embedding, save to database.
        Returns (True, facial_area_dict) or (False, error_str).
        """
        try:
            app   = _get_app()
            faces = app.get(frame_bgr)
        except Exception as exc:
            return False, str(exc)

        if not faces:
            return False, "No face detected."

        face      = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
        embedding = np.array(face.embedding)
        bbox      = face.bbox.astype(int)   # [x1, y1, x2, y2]

        import cv2
        x1, y1, x2, y2 = bbox
        pad = 20
        crop = frame_bgr[
            max(0, y1-pad): min(frame_bgr.shape[0], y2+pad),
            max(0, x1-pad): min(frame_bgr.shape[1], x2+pad),
        ]
        cv2.imwrite(str(FACE_DB / f"{name}.jpg"), crop)

        if name not in self._embeddings:
            self._embeddings[name] = []
        self._embeddings[name].append(embedding)
        self._embeddings[name] = self._embeddings[name][-5:]
        self._save()

        facial_area = {
            "x": int(x1), "y": int(y1),
            "w": int(x2 - x1), "h": int(y2 - y1),
        }
        return True, facial_area

    def identify(self, frame_bgr):
        """
        Returns:
          {"name": str|None, "confidence": float,
           "face_box": (x,y,w,h)|None,
           "status": "match"|"unknown"|"no_face"|"no_enrolled"}
        confidence is normalized to [0, 1].
        """
        self._load()

        if not self._embeddings:
            return {"name": None, "confidence": 0.0,
                    "face_box": None, "status": "no_enrolled"}

        try:
            app   = _get_app()
            faces = app.get(frame_bgr)
        except Exception:
            return {"name": None, "confidence": 0.0,
                    "face_box": None, "status": "no_face"}

        if not faces:
            return {"name": None, "confidence": 0.0,
                    "face_box": None, "status": "no_face"}

        face      = max(faces, key=lambda f: (f.bbox[2]-f.bbox[0]) * (f.bbox[3]-f.bbox[1]))
        embedding = np.array(face.embedding)
        bbox      = face.bbox.astype(int)
        face_box  = (int(bbox[0]), int(bbox[1]),
                     int(bbox[2]-bbox[0]), int(bbox[3]-bbox[1]))

        best_name = None
        best_sim  = -2.0
        for name, embeds in self._embeddings.items():
            sim = max(self._cosine_sim(embedding, e) for e in embeds)
            if sim > best_sim:
                best_sim  = sim
                best_name = name

        # Normalize cosine similarity from [-1,1] → [0,1] for display
        confidence = float((best_sim + 1.0) / 2.0)

        if best_sim >= _SIM_THRESHOLD:
            return {"name": best_name, "confidence": confidence,
                    "face_box": face_box, "status": "match"}
        return {"name": "UNKNOWN", "confidence": confidence,
                "face_box": face_box, "status": "unknown"}

    def has_enrolled(self):
        self._load()
        return bool(self._embeddings)

    @staticmethod
    def _cosine_sim(a, b):
        denom = np.linalg.norm(a) * np.linalg.norm(b)
        return float(np.dot(a, b) / denom) if denom else 0.0
