"""
code_agent.py — G.I.L.'s coding agent.

A Claude-Code-style iterative loop: the model investigates the problem with
free read-only tools (read files, search, list folders, safe commands), and
proposes changes — every file edit and every state-changing terminal command
is posted to the chat and waits for the user's yes/no before it runs.

Tools (one JSON action per turn):
    read_file  {path, start?, lines?}          free
    list_dir   {path}                          free
    search     {pattern, dir}                  free
    run        {command}                       free if read-only, else APPROVAL
    edit_file  {path, old, new}                APPROVAL (shown as a diff)
    write_file {path, content}                 APPROVAL
    done       {summary, success}              finish

Every step is published to the live activity feed; approvals and the final
summary land in the chat. Max 20 steps per task.
"""

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path

import requests

from logger import get as _get_log

log = _get_log("agent")

_GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"
_MAX_STEPS = 20
_APPROVAL_TIMEOUT = 240   # seconds to wait for the user's yes/no

# Commands that run without approval — genuinely read-only or verification.
_FREE_RUN = (
    "git status", "git log", "git diff", "git branch", "git show",
    "dir", "ls", "get-childitem", "get-content", "type ", "cat ",
    "where ", "get-command", "test-path", "findstr", "select-string",
    "python --version", "python -v", "pip list", "pip show",
    "python -m pip list", "python -m pip show", "npm ls", "node --version",
    "pytest", "python -m pytest", "npm test", "python -m py_compile",
    "get-process", "tasklist", "systeminfo", "get-service",
)

_SYSTEM = """\
You are G.I.L.'s coding agent on Windows 11 (PowerShell). Solve the task by
working step by step with tools. Each turn, return ONE JSON object only:
{"tool": "<name>", "args": {...}, "why": "<short reason the user sees>"}

TOOLS:
read_file  {"path": "...", "start": 1, "lines": 120}   read part of a file
list_dir   {"path": "..."}                              list a folder
search     {"pattern": "...", "dir": "..."}             find text in files (recursive)
run        {"command": "..."}                           PowerShell command
edit_file  {"path": "...", "old": "...", "new": "..."}  replace EXACT text (must match once)
write_file {"path": "...", "content": "..."}            create or overwrite a file
done       {"summary": "...", "success": true/false}    finish the task

RULES:
- INVESTIGATE first (read_file/search/run read-only) before changing anything.
- Smallest change that fixes the problem. Match the existing code style.
- File edits and state-changing commands require the user's approval — the
  user sees exactly what you propose. If they decline, try another approach.
- After an edit, VERIFY: run the relevant test / py_compile / build command.
- Absolute Windows paths. Non-interactive commands only. Never destructive
  (no format/shutdown/registry wipes/mass delete).
- "why" is one short sentence, present tense: "Reading the config loader".
- Maximum {steps} steps — be efficient. When solved (or truly stuck), use done.
Return ONLY the JSON object. No markdown, no commentary."""


def _keys() -> list[str]:
    return [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]


def _llm(messages: list) -> dict | None:
    payload = {"model": "llama-3.3-70b-versatile", "messages": messages,
               "temperature": 0.2, "max_tokens": 1600}
    for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
        payload["model"] = model
        for key in _keys():
            try:
                r = requests.post(_GROQ_URL, json=payload,
                                  headers={"Authorization": f"Bearer {key}",
                                           "Content-Type": "application/json"},
                                  timeout=45)
                if r.status_code == 429:
                    continue
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{[\s\S]*\}", raw)
                if m:
                    return json.loads(m.group(0))
            except Exception as exc:
                log.debug("agent llm attempt failed: %s", exc)
    return None


def _is_free_command(cmd: str) -> bool:
    low = cmd.lower().strip()
    return any(low.startswith(p) or f"| {p}" not in low and low == p
               for p in _FREE_RUN) or any(low.startswith(p) for p in _FREE_RUN)


# ── Tool implementations ──────────────────────────────────────────────────────

def _t_read_file(args: dict) -> str:
    p = Path(args.get("path", ""))
    if not p.exists():
        return f"ERROR: {p} does not exist"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception as exc:
        return f"ERROR: {exc}"
    start = max(1, int(args.get("start", 1)))
    count = min(300, int(args.get("lines", 150)))
    chunk = lines[start - 1:start - 1 + count]
    body = "\n".join(f"{i}: {l}" for i, l in enumerate(chunk, start))
    return f"[{p} — {len(lines)} lines total, showing {start}-{start+len(chunk)-1}]\n{body}"


def _t_list_dir(args: dict) -> str:
    p = Path(args.get("path", "."))
    if not p.is_dir():
        return f"ERROR: {p} is not a folder"
    items = []
    for c in sorted(p.iterdir())[:80]:
        items.append(f"{'[D]' if c.is_dir() else '   '} {c.name}")
    return "\n".join(items) or "(empty)"


def _t_search(args: dict) -> str:
    pattern = args.get("pattern", "")
    root = Path(args.get("dir", "."))
    if not pattern or not root.is_dir():
        return "ERROR: need pattern and a valid dir"
    hits = []
    try:
        rx = re.compile(pattern)
    except re.error:
        rx = re.compile(re.escape(pattern))
    for f in root.rglob("*"):
        if len(hits) >= 40:
            break
        if not f.is_file() or f.suffix.lower() in (
                ".exe", ".dll", ".pyd", ".zip", ".jpg", ".png", ".db",
                ".ico", ".pyc"):
            continue
        if any(part in ("__pycache__", ".git", "node_modules", "dist", "venv",
                        ".venv") for part in f.parts):
            continue
        try:
            for n, line in enumerate(
                    f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{f}:{n}: {line.strip()[:140]}")
                    if len(hits) >= 40:
                        break
        except Exception:
            continue
    return "\n".join(hits) or "No matches."


def _t_run(cmd: str) -> str:
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=240,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        return f"[exit {p.returncode}]\n{out[-1800:]}" if out else f"[exit {p.returncode}] (no output)"
    except Exception as exc:
        return f"ERROR: {exc}"


# ── Checkpoints — every file the agent touches is backed up first ─────────────

_BACKUP_ROOT = Path(__file__).parent / "data" / "agent_backups"
_session_backups: dict[str, Path] = {}   # original path -> backup copy


def _checkpoint(p: Path) -> None:
    """Save the pre-edit version of a file (once per session per file)."""
    key = str(p.resolve())
    if key in _session_backups:
        return
    try:
        import shutil, hashlib
        _BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        tag = hashlib.md5(key.encode()).hexdigest()[:10]
        dest = _BACKUP_ROOT / f"{tag}_{p.name}"
        if p.exists():
            shutil.copy2(str(p), str(dest))
        else:
            dest = _BACKUP_ROOT / f"{tag}_{p.name}.NEW"   # marks a created file
            dest.write_text("", encoding="utf-8")
        _session_backups[key] = dest
    except Exception:
        log.debug("checkpoint failed for %s", p, exc_info=True)


def undo_last_changes() -> str:
    """Restore every file the agent modified since GIL started. Deletes files
    the agent created. Called by the 'undo your changes' fast path."""
    if not _session_backups:
        return "Nothing to undo — I haven't changed any files this session."
    import shutil
    restored, removed, failed = 0, 0, 0
    for orig, backup in list(_session_backups.items()):
        try:
            if backup.name.endswith(".NEW"):
                Path(orig).unlink(missing_ok=True)
                removed += 1
            else:
                shutil.copy2(str(backup), orig)
                restored += 1
        except Exception:
            failed += 1
    _session_backups.clear()
    parts = []
    if restored:
        parts.append(f"restored {restored} file{'s' if restored != 1 else ''}")
    if removed:
        parts.append(f"deleted {removed} file{'s' if removed != 1 else ''} I had created")
    msg = "Undone — " + " and ".join(parts) + "." if parts else "Nothing restored."
    if failed:
        msg += f" ({failed} failed — check the backups in data/agent_backups)"
    return msg


def _t_edit_file(args: dict) -> str:
    p = Path(args.get("path", ""))
    old, new = args.get("old", ""), args.get("new", "")
    if not p.exists():
        return f"ERROR: {p} does not exist"
    try:
        src = p.read_text(encoding="utf-8")
    except Exception as exc:
        return f"ERROR: {exc}"
    n = src.count(old)
    if n == 0:
        return "ERROR: old text not found — read the file again and copy it exactly"
    if n > 1:
        return f"ERROR: old text appears {n} times — include more surrounding context"
    _checkpoint(p)
    p.write_text(src.replace(old, new, 1), encoding="utf-8")
    return f"OK — edited {p}"


def _t_write_file(args: dict) -> str:
    p = Path(args.get("path", ""))
    try:
        _checkpoint(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""), encoding="utf-8")
        return f"OK — wrote {p} ({len(args.get('content', ''))} chars)"
    except Exception as exc:
        return f"ERROR: {exc}"


# ── Approval gate ─────────────────────────────────────────────────────────────

def _ask_approval(eng, description: str) -> bool:
    """Post the proposed change in chat and block until the user says yes/no
    (fast_path_handler._fix_confirm routes the answer here)."""
    gate = {"event": threading.Event(), "approved": False}
    eng._agent_gate = gate
    eng.window.add_chat_message(
        "**Approval needed** — I want to run this:\n\n" + description +
        "\n\nSay **yes** to approve or **no** to skip.", "gil")
    try:
        spoken = "I need your approval for the next step — check the chat and say yes or no."
        eng.window.set_state("speaking", said=spoken)
        eng._speak(spoken)
        eng._last_spoke_at[0] = time.time() - 1.5
        eng.window.set_state("listening")
    except Exception:
        pass
    gate["event"].wait(_APPROVAL_TIMEOUT)
    eng._agent_gate = None
    return gate["approved"]


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_agent(eng, task: str, hints: list[str] | None = None) -> None:
    """Execute the agent loop (call from a worker thread)."""
    import activity
    import error_fixer

    window = eng.window
    messages = [{"role": "system", "content": _SYSTEM.replace("{steps}", str(_MAX_STEPS))},
                {"role": "user", "content":
                 f"TASK:\n{task[:2500]}" +
                 (f"\n\nSUGGESTED FIX COMMANDS (verify before using):\n" +
                  "\n".join(hints) if hints else "")}]

    activity.set_group("Coding agent")
    edits_made, verified = 0, False
    try:
        for step in range(1, _MAX_STEPS + 1):
            action = _llm(messages)
            if not action or "tool" not in action:
                window.add_chat_message(
                    "I lost the thread mid-task (model returned garbage). "
                    "Ask me to try again.", "gil")
                return
            tool = str(action.get("tool", ""))
            args = action.get("args") or {}
            why  = str(action.get("why", tool))[:90]

            if tool == "done":
                summary = str(args.get("summary", "Task finished."))[:600]
                ok = bool(args.get("success", True))
                activity.instant("code", "Agent finished",
                                 summary[:100], status="done" if ok else "fail")
                window.add_chat_message(
                    ("**Solved** ✓ — " if ok else "**Stopped** — ") + summary +
                    (f"\n\n({edits_made} file change{'s' if edits_made != 1 else ''} applied)"
                     if edits_made else ""), "gil")
                spoken = summary.split(".")[0][:140] + "."
                window.set_state("speaking", said=spoken)
                eng._speak(spoken)
                eng._last_spoke_at[0] = time.time() - 1.5
                eng._last_said[0] = spoken
                return

            # ---- execute the tool ----
            needs_approval = False
            desc = ""
            if tool == "run":
                cmd = str(args.get("command", ""))
                if error_fixer.is_blocked(cmd):
                    result = "ERROR: command blocked — too destructive"
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content": f"RESULT:\n{result}"})
                    continue
                needs_approval = not _is_free_command(cmd)
                desc = f"```powershell\n{cmd}\n```"
            elif tool == "edit_file":
                needs_approval = True
                desc = (f"Edit `{args.get('path','?')}`:\n\n**Remove:**\n```\n"
                        f"{str(args.get('old',''))[:800]}\n```\n**Insert:**\n```\n"
                        f"{str(args.get('new',''))[:800]}\n```")
            elif tool == "write_file":
                needs_approval = True
                content = str(args.get("content", ""))
                desc = (f"Write `{args.get('path','?')}` "
                        f"({len(content)} chars):\n```\n{content[:900]}"
                        f"{'…' if len(content) > 900 else ''}\n```")

            if needs_approval:
                aid = activity.start("code", f"Waiting for approval: {why}")
                approved = _ask_approval(eng, f"*{why}*\n\n{desc}")
                if not approved:
                    activity.fail(aid, "declined by user")
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content":
                                     "RESULT:\nUser DECLINED this change. Try a "
                                     "different approach or finish with done."})
                    continue
                activity.done(aid, "approved")

            # Show exactly what GIL is touching — path/dir/pattern/command —
            # not just the reason, so the user sees where he is working (Claude-style).
            _tgt = (args.get("path") or args.get("dir")
                    or args.get("command") or args.get("pattern") or "")
            _kind = {"read_file": "file", "list_dir": "file", "search": "search",
                     "run": "code", "edit_file": "code",
                     "write_file": "code"}.get(tool, "code")
            _verb = {"read_file": "Reading", "list_dir": "Listing",
                     "search": "Searching", "run": "Running",
                     "edit_file": "Editing", "write_file": "Writing"}.get(tool, why)
            _label = f"[{step}] {_verb} {_tgt}".strip() if _tgt else f"[{step}] {why}"
            aid = activity.start(_kind, _label[:100])
            try:
                if tool == "read_file":
                    result = _t_read_file(args)
                elif tool == "list_dir":
                    result = _t_list_dir(args)
                elif tool == "search":
                    result = _t_search(args)
                elif tool == "run":
                    result = _t_run(str(args.get("command", "")))
                    if any(v in str(args.get("command", "")).lower()
                           for v in ("pytest", "py_compile", "npm test", "build")):
                        verified = "exit 0" in result
                elif tool == "edit_file":
                    result = _t_edit_file(args)
                    if result.startswith("OK"):
                        edits_made += 1
                elif tool == "write_file":
                    result = _t_write_file(args)
                    if result.startswith("OK"):
                        edits_made += 1
                else:
                    result = f"ERROR: unknown tool {tool!r}"
            except Exception as exc:
                result = f"ERROR: {exc}"

            ok = not result.startswith("ERROR")
            (activity.done if ok else activity.fail)(aid, result[:110])

            messages.append({"role": "assistant", "content": json.dumps(action)})
            messages.append({"role": "user", "content": f"RESULT:\n{result[:2200]}"})
            # keep context bounded: system + task + last 16 exchanges
            if len(messages) > 34:
                messages = messages[:2] + messages[-32:]

        window.add_chat_message(
            f"I hit my {_MAX_STEPS}-step limit. Applied {edits_made} change(s) so far — "
            "tell me to continue if you want me to keep going.", "gil")
    finally:
        import activity as _a
        _a.clear_group()
        eng._agent_gate = None
        try:
            from ears import unmute
            unmute()
        except Exception:
            pass
        window.set_state("listening")


def run_async(eng, task: str, hints: list[str] | None = None) -> None:
    threading.Thread(target=run_agent, args=(eng, task, hints),
                     daemon=True, name="GIL-CodeAgent").start()
