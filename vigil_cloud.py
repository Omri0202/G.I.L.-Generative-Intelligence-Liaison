"""
vigil_cloud.py — Vigil on a server of its own, so it keeps going with the PC off.

The host (Render's free plan) runs `python vigil_cloud.py`. It:

  1. brings back Vigil's memory from the private store (cloud_store.py),
  2. runs the Vigil chat and its checks exactly as the PC does — the phone
     app, notifications, mail, comparisons — with the PC joining through its
     link (vigil_link.py) whenever it's on,
  3. saves its memory back every couple of minutes and when the host stops it,
  4. and pings itself so the free plan never puts it to sleep.

Run it only on the server: GIL_CLOUD=1 makes vigil_chat serve the public
internet with its key checks, and there's no PC under it.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path

os.environ.setdefault("GIL_CLOUD", "1")

ROOT = Path(__file__).parent
DATA = ROOT / "data"
SAVE_SECS = 120
AWAKE_SECS = 300             # the free plan sleeps after 15 min with no visitors
RESTORE_TRIES = (5, 15, 30, 60, 120)

import logger  # noqa: E402 — after GIL_CLOUD, before anything logs

logger.log.addHandler(logger._ch)              # the host's log is stdout
log = logger.get("vigil.cloud")

_store = None
_stopping = threading.Event()


def _restore():
    """The store, once it's known what it holds — or None, and then nothing's
    saved: an empty server mustn't overwrite the real memory it couldn't read."""
    import cloud_store
    store = cloud_store.Store.from_env(DATA)
    if store is None:
        log.warning("no store configured — Vigil forgets everything when the server restarts")
        return None
    missing = cloud_store.available()
    if missing:
        log.error("can't reach the store: %s not installed here", missing)
        return None
    for wait in RESTORE_TRIES:
        try:
            log.info("store: %s", store.restore())
            return store
        except cloud_store.StoreError as exc:
            log.error("store: couldn't restore (%s) — trying again in %ds", exc, wait)
            time.sleep(wait)
    log.error("store: giving up — running without saving, so the real memory stays safe")
    return None


def _keeper() -> None:
    while not _stopping.wait(SAVE_SECS):
        try:
            _store.save()
        except Exception as exc:
            log.error("store: save failed (%s)", exc)


def _keep_awake(host: str) -> None:
    import requests
    while not _stopping.wait(AWAKE_SECS):
        try:
            requests.get(f"https://{host}/health", timeout=20)
        except requests.RequestException as exc:
            log.info("keep-awake ping failed (%s)", type(exc).__name__)


def _on_stop(signum, _frame) -> None:
    """The host is stopping the server (a deploy, a restart): save first."""
    log.info("stopping (signal %s) — saving", signum)
    _stopping.set()
    if _store is not None:
        try:
            _store.save()
        except Exception as exc:
            log.error("store: last save failed (%s)", exc)
    logging.shutdown()
    os._exit(0)


def main() -> None:
    global _store
    if not os.environ.get("GIL_VIGIL_KEY"):
        sys.exit("GIL_VIGIL_KEY isn't set — the chat would have no lock on it")
    DATA.mkdir(parents=True, exist_ok=True)
    log.info("Vigil in the cloud starting — local time %s", time.strftime("%Y-%m-%d %H:%M %Z"))
    _store = _restore()

    import vigil
    import vigil_chat
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):           # SIGBREAK: a local run on Windows
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _on_stop)
    vigil_chat.start(os.environ.get("GIL_USER", ""))
    vigil.start()
    log.info("serving on %s", vigil_chat.url())
    if _store is not None:
        threading.Thread(target=_keeper, daemon=True, name="GIL-Store").start()
    if vigil_chat.CLOUD_HOST:
        threading.Thread(target=_keep_awake, args=(vigil_chat.CLOUD_HOST,), daemon=True,
                         name="GIL-Awake").start()
    while not _stopping.wait(1):              # short beats: a stop signal is handled between them
        pass


if __name__ == "__main__":
    main()
