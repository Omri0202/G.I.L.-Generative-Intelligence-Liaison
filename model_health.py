"""
model_health.py — are the models G.I.L. is set to use still served?

Groq retires models without warning. By autumn 2026 it had retired Llama 3.1
8B, Llama 3.3 70B, qwen3.6 (GIL's eyes), compound and several others, and
nothing in GIL noticed for months. The tests mock the network, so they stayed
green while vision, memory learning, the website builder, 3D Studio and half
of the router 404'd on every call.

One free request to /v1/models settles it. This module lists the models GIL
is configured to use, asks Groq which ones it still serves, and says which are
gone. main.py runs it at startup and tells the user once; tests/
test_models_live.py also scans the source, so a hard-coded retired model fails
the suite instead of shipping.
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import requests

from logger import get as _get_log

log = _get_log("models")

ROOT = Path(__file__).parent
_URL = "https://api.groq.com/openai/v1/models"
_TTL = 6 * 3600          # the list changes over weeks, not seconds
_cache: dict = {}

#: A quoted string shaped like a Groq model id. Owners are limited to the ones
#: Groq serves, so OpenRouter ids ("google/…:free") aren't mistaken for Groq's.
_GROQ_ID = re.compile(
    r"""["']((?:openai|qwen|meta-llama|moonshotai|groq)/[A-Za-z0-9._\-]+"""
    r"""|(?:llama|gemma|mixtral|allam|whisper|distil-whisper|deepseek-r1)"""
    r"""[A-Za-z0-9._\-]*\d[A-Za-z0-9._\-]*)["']""")


def live(force: bool = False) -> set[str] | None:
    """Model ids Groq serves right now, or None when it can't be asked."""
    if not force and _cache.get("at", 0) + _TTL > time.time():
        return _cache["ids"]
    key = os.getenv("GROQ_API_KEY") or os.getenv("GROQ_API_KEY_2")
    if not key:
        return None
    try:
        r = requests.get(_URL, headers={"Authorization": f"Bearer {key}"}, timeout=15)
        r.raise_for_status()
        ids = {m["id"] for m in r.json().get("data", [])}
    except Exception:
        log.debug("Groq model list unavailable", exc_info=True)
        return None
    _cache.update(at=time.time(), ids=ids)
    return ids


def configured() -> dict[str, list[str]]:
    """model id -> what GIL uses it for, read from the modules that choose."""
    uses: dict[str, list[str]] = {}

    def add(model: str, where: str) -> None:
        if model and where not in uses.setdefault(model, []):
            uses[model].append(where)

    try:
        import model_router
        for lane, spec in model_router.LANES.items():
            add(spec["model"], f"the {spec['label'].lower()} lane")
    except Exception:
        log.debug("router models unavailable", exc_info=True)
    try:
        import gil_brain
        add(gil_brain.GROQ_MODEL, "the brain")
        for m in gil_brain._FALLBACK_MODELS:
            add(m, "brain fallbacks")
        for m in gil_brain.MODELS:
            add(m["id"], "the model menu")
    except Exception:
        log.debug("brain models unavailable", exc_info=True)
    try:
        import vision_model
        add(vision_model.MODEL, "reading images")
    except Exception:
        log.debug("vision model unavailable", exc_info=True)
    try:
        import code_agent
        for m in code_agent._agent_models():
            add(m, "the coding agent")
    except Exception:
        log.debug("agent models unavailable", exc_info=True)
    try:
        import vigil
        for m in vigil.MODELS:
            add(m, "Vigil")
    except Exception:
        log.debug("vigil models unavailable", exc_info=True)
    return uses


def dead(force: bool = False) -> dict[str, list[str]] | None:
    """Configured models Groq no longer serves; None when Groq can't be asked."""
    ids = live(force)
    if ids is None:
        return None
    return {m: w for m, w in configured().items() if m not in ids}


def in_source(root: Path = ROOT) -> dict[str, list[str]]:
    """Every Groq-shaped model id written in GIL's own .py files, including
    the hard-coded calls no settings module lists. Needs a source checkout."""
    found: dict[str, list[str]] = {}
    for path in sorted(root.glob("*.py")):
        if path.name.startswith("_"):
            continue                       # untracked probes and scratch files
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore")
                                 .splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            for model in _GROQ_ID.findall(line):
                found.setdefault(model, []).append(f"{path.name}:{n}")
    return found


def report() -> str:
    """One message for the user when something GIL relies on is gone, else ''."""
    gone = dead()
    if not gone:
        return ""
    names = ", ".join(sorted(gone))
    uses = sorted({w for ws in gone.values() for w in ws})
    more = "…" if len(uses) > 4 else ""
    return (f"Heads up: Groq no longer serves {names}, which I use for "
            f"{', '.join(uses[:4])}{more}. I'll fall back to other models where I "
            "can, but that part of me needs updating.")
