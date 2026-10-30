"""
token_meter.py — what GIL has spent, and how close he is to running dry.

The free tier caps tokens per MINUTE and per DAY, per model. Hitting either
looks identical from the outside — GIL simply stops working — so this tracks
real usage from the API's own `usage` field and exposes it as something the UI
can show and warn about before the wall is hit.

State lives in data/token_usage.json so the daily figure survives restarts.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date
from pathlib import Path

from logger import get as _get_log

log = _get_log("tokens")

_FILE = Path(__file__).parent / "data" / "token_usage.json"
_lock = threading.Lock()

#: Free-tier ceilings. Per model, since they differ — the vision model has a far
#: smaller daily allowance than the text brain, which is why images run out first.
LIMITS = {
    "openai/gpt-oss-120b":  {"tpm": 8_000,  "tpd": 500_000},
    "openai/gpt-oss-20b":   {"tpm": 8_000,  "tpd": 500_000},
    # qwen3.8's daily figure is assumed equal to the qwen3.6 it replaced: Groq's
    # headers report per-minute tokens and daily requests, not daily tokens.
    "qwen/qwen3.8-27b":     {"tpm": 8_000,  "tpd": 200_000},
}
_DEFAULT = {"tpm": 8_000, "tpd": 200_000}

#: Warn once when a budget crosses these fractions.
_WARN_AT = (0.80, 0.95)

_state = {
    "day": "",
    "daily": {},        # model -> tokens today
    "recent": [],       # [(epoch, model, tokens)] — last ~2 minutes, for TPM
    "warned": {},       # "model:0.8" -> True, so a warning fires once per level
}
_listeners = []


def _load():
    global _state
    try:
        if _FILE.is_file():
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _state.update({k: data.get(k, _state[k]) for k in
                               ("day", "daily", "warned")})
    except Exception:
        log.debug("could not read usage file", exc_info=True)
    _roll_day()


def _save():
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        _FILE.write_text(json.dumps({"day": _state["day"],
                                     "daily": _state["daily"],
                                     "warned": _state["warned"]}),
                         encoding="utf-8")
    except Exception:
        log.debug("could not write usage file", exc_info=True)


def _roll_day():
    """A new day resets the daily counters (and the warnings with them)."""
    today = date.today().isoformat()
    if _state["day"] != today:
        _state["day"] = today
        _state["daily"] = {}
        _state["warned"] = {}
        _save()


def subscribe(fn):
    """Call `fn(status_dict)` whenever usage changes — the UI hooks in here."""
    if fn not in _listeners:
        _listeners.append(fn)


def mark_day_full(model: str) -> None:
    """Record that the provider itself says this model's day is spent.

    The meter only ever sees what GIL spends, so anything else on the same
    account — another tool, a script, a second app — is invisible to it. That
    is how the ring came to read 48% while Groq was refusing calls at 99%: the
    number was honest about GIL's own usage and useless as a picture of the
    account. When the provider tells us the truth outright, believe it over our
    own bookkeeping."""
    with _lock:
        _roll_day()
        model = model or "unknown"
        lim = LIMITS.get(model, _DEFAULT)["tpd"]
        _state["daily"][model] = max(int(_state["daily"].get(model, 0)), lim)
        _save()


def record(model: str, tokens: int, prompt: int = 0, completion: int = 0):
    """Log real usage from one API call."""
    if not tokens or tokens <= 0:
        return
    with _lock:
        _roll_day()
        model = model or "unknown"
        _state["daily"][model] = _state["daily"].get(model, 0) + int(tokens)
        now = time.time()
        _state["recent"].append((now, model, int(tokens)))
        # keep only the last two minutes; that's all TPM needs
        cutoff = now - 120
        _state["recent"] = [r for r in _state["recent"] if r[0] >= cutoff]
        _save()
        st = _status_locked(model)
        crossed = _check_warnings_locked(model, st)
    for fn in list(_listeners):
        try:
            fn(st, crossed)
        except Exception:
            log.debug("usage listener failed", exc_info=True)


def _minute_used(model: str) -> int:
    cutoff = time.time() - 60
    return sum(t for ts, m, t in _state["recent"] if ts >= cutoff and m == model)


def _status_locked(model: str) -> dict:
    lim = LIMITS.get(model, _DEFAULT)
    day_used = int(_state["daily"].get(model, 0))
    min_used = _minute_used(model)
    return {
        "model": model,
        "minute": {"used": min_used, "limit": lim["tpm"],
                   "pct": min(1.0, min_used / lim["tpm"]) if lim["tpm"] else 0.0},
        "day": {"used": day_used, "limit": lim["tpd"],
                "pct": min(1.0, day_used / lim["tpd"]) if lim["tpd"] else 0.0},
    }


def status(model: str = "openai/gpt-oss-120b") -> dict:
    with _lock:
        _roll_day()
        return _status_locked(model)


def all_status() -> list:
    """Every model touched today — what the gauges render."""
    with _lock:
        _roll_day()
        models = set(_state["daily"]) | {m for _, m, _ in _state["recent"]}
        return [_status_locked(m) for m in sorted(models)]


def _check_warnings_locked(model: str, st: dict):
    """Return a message when the DAY budget first crosses a warning level.

    Day only, deliberately. The per-minute cap is still enforced — exhausted()
    checks it and the brain falls back to another model when it bites — but it
    is never mentioned to the user. Reporting two budgets with different
    periods made the percentage meaningless: it jumped between them with no
    visible cause, reading 12% one moment and 85% the next. A per-minute rate
    limit is a detail of how the provider bills; it is not something anyone can
    act on, and it resets before you could.
    """
    for scope, label in (("day", "today's"),):
        pct = st[scope]["pct"]
        for level in _WARN_AT:
            key = f"{model}:{scope}:{level}"
            if pct >= level and not _state["warned"].get(key):
                _state["warned"][key] = True
                _save()
                short = model.split("/")[-1]
                if pct >= 1.0:
                    return (f"I've used all of {label} tokens for `{short}`. "
                            "They reset at midnight.")
                return (f"Heads up — {int(pct * 100)}% of {label} tokens for "
                        f"`{short}` are gone.")
    return None


def exhausted(model: str) -> bool:
    st = status(model)
    return st["day"]["pct"] >= 1.0 or st["minute"]["pct"] >= 1.0


_load()
