"""
error_fixer.py — G.I.L. error analysis & repair.

Flow (all inside the chat):
  1. GIL spots an error (on screen, or the user pastes/describes one).
  2. analyze() asks the 70B model for a diagnosis + a concrete fix:
     either shell commands GIL can run himself, or manual steps.
  3. GIL posts the diagnosis in chat and asks permission.
  4. On "yes", execute() runs the commands one by one — each shows up in
     the live activity feed — and GIL reports what happened.

Safety: commands are checked against a destructive-pattern blocklist before
running, permission is always explicit, and output is truncated.
"""

import json
import os
import re
import subprocess
import time

import requests

from logger import get as _get_log

log = _get_log("fixer")

_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

# Never run these even with permission — too destructive for an assistant.
_BLOCKED = (
    "format ", "rm -rf /", "rm -rf ~", "rmdir /s c:", "del /f /s /q c:",
    "reg delete hklm", "diskpart", "cipher /w", "mkfs", "dd if=",
    "shutdown", "bcdedit", "vssadmin delete", "takeown /f c:",
)

_ANALYZE_SYSTEM = """\
You are G.I.L., a senior Windows engineer. The user hit an error. Diagnose it
and produce a fix. Return ONE JSON object, nothing else:
{"diagnosis": "1-2 sentence root cause in plain language",
 "fix_type": "auto" or "manual",
 "commands": ["shell command 1", "..."],
 "steps": ["manual step 1", "..."],
 "confidence": "high" | "medium" | "low"}

Rules:
- "auto" + commands: ONLY when the fix is safe, non-interactive PowerShell/cmd
  commands (installing a package, clearing a cache, restarting a service,
  killing a stuck process, fixing a config value, git operations).
- "manual" + steps: when it needs UI clicks, credentials, reboots, or judgment.
- Commands must be non-interactive (no prompts). Prefer specific over broad.
- NEVER: formatting drives, deleting user data, registry-wide changes,
  disabling security. If the only fix is destructive, use "manual".
- Windows 11, PowerShell available. Python is "python" (pip via "python -m pip")."""


def _keys() -> list[str]:
    return [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]


def analyze(error_text: str, screen_context: str = "") -> dict | None:
    """Ask the 70B model to diagnose the error. Returns the parsed dict or None."""
    user = f"ERROR / PROBLEM:\n{error_text.strip()[:2000]}"
    if screen_context:
        user += f"\n\nWHAT'S ON SCREEN RIGHT NOW:\n{screen_context[:1500]}"
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [{"role": "system", "content": _ANALYZE_SYSTEM},
                     {"role": "user",   "content": user}],
        "temperature": 0.2,
        "max_tokens": 700,
    }
    for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
        payload["model"] = model
        for key in _keys():
            try:
                r = requests.post(
                    _GROQ_URL, json=payload,
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    timeout=25)
                if r.status_code == 429:
                    continue
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{[\s\S]*\}", raw)
                if not m:
                    continue
                data = json.loads(m.group(0))
                data.setdefault("commands", [])
                data.setdefault("steps", [])
                data.setdefault("fix_type", "manual")
                return data
            except Exception as exc:
                log.debug("analyze attempt failed: %s", exc)
    return None


def is_blocked(cmd: str) -> bool:
    low = f" {cmd.lower().strip()} "
    return any(b in low for b in _BLOCKED)


def execute(commands: list[str]) -> list[tuple[str, bool, str]]:
    """
    Run fix commands sequentially with live activity events.
    Returns [(command, ok, output_tail), ...]. Stops on first failure.
    """
    try:
        import activity
    except Exception:
        activity = None
    results = []
    for cmd in commands[:8]:
        cmd = cmd.strip()
        if not cmd:
            continue
        if is_blocked(cmd):
            results.append((cmd, False, "blocked — too destructive for auto-fix"))
            if activity:
                activity.instant("code", f"Blocked: {cmd[:50]}",
                                 "destructive command refused", status="fail")
            continue
        aid = activity.start("code", f"Running: {cmd[:60]}") if activity else None
        try:
            p = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
                capture_output=True, text=True, timeout=180,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            ok  = p.returncode == 0
            tail = out[-400:] if out else ""
            results.append((cmd, ok, tail))
            if activity and aid is not None:
                (activity.done if ok else activity.fail)(aid, tail[:120])
            if not ok:
                break
        except Exception as exc:
            results.append((cmd, False, str(exc)[:200]))
            if activity and aid is not None:
                activity.fail(aid, str(exc)[:120])
            break
    return results


def summarize(results: list[tuple[str, bool, str]]) -> str:
    """Short spoken/chat summary of an executed fix."""
    if not results:
        return "Nothing was run."
    ok = sum(1 for _, s, _ in results if s)
    n  = len(results)
    if ok == n:
        return (f"Fix applied — all {n} command{'s' if n > 1 else ''} succeeded. "
                "Try again and tell me if the error is gone.")
    failed = next((c for c, s, o in results if not s), "")
    return (f"Ran {ok} of {n} commands, then '{failed[:60]}' failed. "
            "Check the details in the chat — I stopped there to be safe.")


def format_report(analysis: dict) -> str:
    """Chat-friendly markdown report of the diagnosis + plan."""
    lines = [f"**Diagnosis:** {analysis.get('diagnosis', 'Unknown').strip()}"]
    conf = analysis.get("confidence")
    if conf:
        lines.append(f"**Confidence:** {conf}")
    if analysis.get("fix_type") == "auto" and analysis.get("commands"):
        lines.append("\n**My fix plan:**")
        for i, c in enumerate(analysis["commands"][:8], 1):
            lines.append(f"{i}. `{c}`")
        lines.append("\nSay **yes** and I'll run it, or **no** to skip.")
    elif analysis.get("steps"):
        lines.append("\n**Here's what to do:**")
        for i, s in enumerate(analysis["steps"][:8], 1):
            lines.append(f"{i}. {s}")
    return "\n".join(lines)
