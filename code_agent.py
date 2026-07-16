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
- INVESTIGATE first: read the exact code (read_file/search) before editing.
  Never edit a file you have not read this session.
- Smallest change that actually fixes the problem. Match the existing style.
- Every edit MUST change real behaviour. NEVER propose cosmetic or no-op edits
  (e.g. adding an unused `except X as e`, re-quoting, or reformatting only).
- Your edits are AUTO-CHECKED before they apply: the proposed file is syntax-
  checked (py_compile / JSON parse / bracket balance) and rejected if it would
  break, and auto-reverted if a landed change breaks the file. So get it right —
  copy `old` text exactly (it must match once) and keep the result valid.
- File edits and state-changing commands need the user's approval — they see a
  diff of exactly what you propose. If they decline, try a different approach.
- After editing, VERIFY: run the relevant py_compile / test / build command and
  read the result before calling done.
- Absolute Windows paths. Non-interactive commands only. Never destructive
  (no format/shutdown/registry wipes/mass delete).
- "why" is one short sentence, present tense: "Reading the config loader".
- Maximum {steps} steps — be efficient. When solved (or truly stuck), use done.
Return ONLY the JSON object. No markdown, no commentary."""


def _keys() -> list[str]:
    return [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]


def _extract_action(raw: str) -> dict | None:
    """Pull one JSON action object out of a model reply, tolerantly.
    Handles ```json fences, leading prose, and multiple/!nested braces by
    scanning for the first brace-balanced span that parses and has a 'tool'."""
    if not raw:
        return None
    txt = raw.strip()
    # strip a ```json … ``` (or plain ```) fence if present
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt)
    if fence:
        txt = fence.group(1).strip()
    # fast path: the whole thing is the object
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict) and "tool" in obj:
            return obj
    except Exception:
        pass
    # scan for balanced {...} spans and return the first that parses w/ a tool
    for start in (i for i, c in enumerate(txt) if c == "{"):
        depth, instr, esc = 0, False, False
        for j in range(start, len(txt)):
            c = txt[j]
            if esc:
                esc = False; continue
            if c == "\\":
                esc = True; continue
            if c == '"':
                instr = not instr
            elif not instr and c == "{":
                depth += 1
            elif not instr and c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(txt[start:j + 1])
                        if isinstance(obj, dict) and "tool" in obj:
                            return obj
                    except Exception:
                        pass
                    break
    return None


def _llm(messages: list) -> dict | None:
    payload = {"model": "llama-3.3-70b-versatile", "messages": messages,
               "temperature": 0.2, "max_tokens": 1600,
               "response_format": {"type": "json_object"}}
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
                # json_object mode can 400 on some models — retry without it
                if r.status_code == 400 and "response_format" in payload:
                    p2 = {k: v for k, v in payload.items() if k != "response_format"}
                    r = requests.post(_GROQ_URL, json=p2,
                                      headers={"Authorization": f"Bearer {key}",
                                               "Content-Type": "application/json"},
                                      timeout=45)
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                act = _extract_action(raw)
                if act:
                    return act
                log.debug("agent llm: no parseable action in reply: %.160s", raw)
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


# ── Self-review of proposed edits (so GIL earns trust) ──────────────────────────

def _bracket_error(src: str) -> str | None:
    """Heuristic brace/paren/bracket + string balance for JS/TS/HTML/CSS.
    Skips strings (incl. template literals with ${…}), // /* */ and <!-- -->
    comments, AND /regex/ literals (with char classes). Regex-vs-division is
    decided from the previous significant char. Only reports high-confidence
    breakage so it never rejects a valid edit."""
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = set("([{")
    stack, tmpl = [], []          # tmpl: nesting depth where a `…${ opened
    i, n, quote = 0, len(src), None
    prev = ""                     # last significant (non-space) char seen
    while i < n:
        c = src[i]
        if quote:
            if c == "\\":
                i += 2; continue
            if quote == "`" and c == "$" and i + 1 < n and src[i + 1] == "{":
                tmpl.append(len(stack)); stack.append("{"); quote = None
                prev = "{"; i += 2; continue
            if c == quote:
                quote = None
            i += 1; prev = '"'; continue
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            i = (end + 2) if end != -1 else n; continue
        if src[i:i + 4] == "<!--":
            end = src.find("-->", i + 4)
            i = (end + 3) if end != -1 else n; continue
        if c == "/" and (prev == "" or prev in "([{,;:=!&|?+-*%^~<>"):
            # regex literal — scan to the closing unescaped '/', respecting [..]
            i += 1; incls = False
            while i < n:
                rc = src[i]
                if rc == "\\":
                    i += 2; continue
                if rc == "\n":
                    break                      # not a regex after all; stop
                if rc == "[":
                    incls = True
                elif rc == "]":
                    incls = False
                elif rc == "/" and not incls:
                    i += 1; break
                i += 1
            prev = "/"; continue
        if c in ("'", '"', "`"):
            quote = c; i += 1; continue
        if c in opens:
            stack.append(c); prev = c; i += 1; continue
        if c in pairs:
            if not stack or stack[-1] != pairs[c]:
                return f"unbalanced '{c}'"
            stack.pop()
            if c == "}" and tmpl and len(stack) == tmpl[-1]:
                tmpl.pop(); quote = "`"          # back into the template string
            prev = c; i += 1; continue
        if not c.isspace():
            prev = c
        i += 1
    if quote and quote != "`":
        return f"unterminated string ({quote})"
    if stack:
        return f"unclosed '{stack[-1]}'"
    return None


def _validate_syntax(path: str, content: str) -> str | None:
    """Return why `content` is broken for its file type, else None. Static only —
    never runs the code. Backs GIL's 'reject my own broken edit' guarantee."""
    ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    try:
        if ext == ".py":
            compile(content, path or "<edit>", "exec")
        elif ext == ".json":
            json.loads(content)
        elif ext in (".js", ".jsx", ".mjs", ".ts", ".tsx",
                     ".html", ".htm", ".css"):
            return _bracket_error(content)
    except SyntaxError as exc:
        return f"Python syntax error (line {exc.lineno}): {exc.msg}"
    except json.JSONDecodeError as exc:
        return f"invalid JSON (line {exc.lineno}): {exc.msg}"
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"
    return None


def _pointless_edit(old: str, new: str) -> str | None:
    """Catch edits that change nothing meaningful (the trust-killers)."""
    if new == old:
        return "the new text is identical to the old text"
    # adds `except X as e:` where the bound name is never used → pure churn
    m = re.search(r"except\b[^\n:]*\bas\s+([A-Za-z_]\w*)\s*:", new)
    if m:
        name = m.group(1)
        without = re.sub(r"\s+as\s+" + re.escape(name) + r"\s*:", ":", new, count=1)
        uses = len(re.findall(r"\b" + re.escape(name) + r"\b", new))
        if without.strip() == old.strip() and uses <= 1:
            return (f"this only adds an exception variable '{name}' that is never "
                    "used — it changes nothing")
    return None


def _restore_one(p: Path) -> bool:
    """Revert a single file to its pre-edit checkpoint (auto-revert safety net)."""
    backup = _session_backups.get(str(p.resolve()))
    if not backup:
        return False
    try:
        import shutil
        if backup.name.endswith(".NEW"):
            p.unlink(missing_ok=True)
        else:
            shutil.copy2(str(backup), str(p))
        return True
    except Exception:
        log.exception("auto-revert failed for %s", p)
        return False


def _verify_or_revert(path: str, ok_result: str) -> str:
    """After an edit lands, re-check the file ON DISK. If it's broken, restore
    the checkpoint automatically so GIL never leaves broken code behind."""
    p = Path(str(path))
    try:
        content = p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ok_result
    broke = _validate_syntax(str(path), content)
    if broke:
        reverted = _restore_one(p)
        return (f"ERROR: reverted — the change broke {p.name} ({broke})."
                + ("" if reverted else " (could not auto-restore — check backups)"))
    return ok_result


def _precheck_change(tool: str, args: dict) -> str | None:
    """Static safety review of a proposed edit BEFORE the user ever sees it.
    Returns a rejection reason, or None if the change is sound and worth showing."""
    path = str(args.get("path", ""))
    if tool == "edit_file":
        p = Path(path)
        if not p.exists():
            return f"{p.name} doesn't exist yet — read or write it, don't edit it"
        try:
            src = p.read_text(encoding="utf-8")
        except Exception as exc:
            return f"can't read {p.name}: {exc}"
        old, new = str(args.get("old", "")), str(args.get("new", ""))
        cnt = src.count(old) if old else 0
        if cnt == 0:
            return "the 'old' text isn't in the file — read it again and copy it exactly"
        if cnt > 1:
            return (f"the 'old' text appears {cnt} times — add surrounding context "
                    "so it matches exactly once")
        pointless = _pointless_edit(old, new)
        if pointless:
            return pointless
        broke = _validate_syntax(path, src.replace(old, new, 1))
        if broke:
            return f"this edit would break {p.name} — {broke}"
        return None
    if tool == "write_file":
        content = str(args.get("content", ""))
        p = Path(path)
        if p.exists():
            try:
                if p.read_text(encoding="utf-8") == content:
                    return "the file already has exactly this content — nothing to do"
            except Exception:
                pass
        broke = _validate_syntax(path, content)
        if broke:
            return f"this would write a broken {p.name} — {broke}"
        return None
    return None


# ── Approval gate ─────────────────────────────────────────────────────────────

def _ask_approval(eng, description: str, diff: dict | None = None,
                  file: str | None = None) -> bool:
    """Post the proposed change and block until the user decides. In the web
    chat this renders clickable Approve/Reject buttons + a unified diff
    (Claude-style); the voice/text "yes"/"no" path (fast_path_handler.
    _fix_confirm) still works and resolves the same gate."""
    gate = {"event": threading.Event(), "approved": False}
    eng._agent_gate = gate
    win = eng.window
    # Clickable buttons when the web UI is available; text prompt otherwise.
    if hasattr(win, "request_approval"):
        win.request_approval("**Approval needed** — I want to run this:\n\n"
                             + description, kind="agent", diff=diff, file=file)
    else:
        win.add_chat_message(
            "**Approval needed** — I want to run this:\n\n" + description +
            "\n\nSay **yes** to approve or **no** to skip.", "gil")
    # In /codemode this is a typed developer surface — GIL must NOT speak the
    # prompt. Speaking made the mic hear GIL and transcribe his own words into
    # the user's next message. Approval is click-only (or typed yes/no).
    if not getattr(eng, "_code_mode", False):
        try:
            spoken = "I need your approval for the next step — approve or skip it in the chat."
            win.set_state("speaking", said=spoken)
            eng._speak(spoken)
            eng._last_spoke_at[0] = time.time() - 1.5
            eng._last_said[0]     = spoken   # let the echo filter suppress it
            win.set_state("listening")
        except Exception:
            pass
    gate["event"].wait(_APPROVAL_TIMEOUT)
    eng._agent_gate = None
    return gate["approved"]


# ── Main loop ─────────────────────────────────────────────────────────────────

def _known_locations() -> str:
    """Real paths the agent should use instead of guessing where things live."""
    gil_root = str(Path(__file__).parent)
    lines = [
        f"- G.I.L.'s own project (\"the gil project\", \"this project\", "
        f"\"your code\"): {gil_root}",
        f"- Current working directory: {os.getcwd()}",
    ]
    try:
        home = Path.home()
        for name in ("Desktop", "Documents", "Downloads"):
            p = home / name
            if p.is_dir():
                lines.append(f"- {name}: {p}")
        # desktop project folders (with a .git or common project markers)
        desk = home / "Desktop"
        if desk.is_dir():
            projs = [d.name for d in desk.iterdir()
                     if d.is_dir() and (d / ".git").exists()][:8]
            if projs:
                lines.append("- Desktop project folders: " + ", ".join(projs))
    except Exception:
        pass
    return "\n".join(lines)


def _lang_for(path: str) -> str:
    ext = ("." + path.rsplit(".", 1)[-1].lower()) if "." in path else ""
    return {".py": "python", ".js": "javascript", ".ts": "typescript",
            ".jsx": "javascript", ".tsx": "typescript", ".html": "html",
            ".css": "css", ".json": "json", ".sh": "bash", ".rs": "rust",
            ".go": "go", ".java": "java", ".c": "c", ".cpp": "cpp",
            ".swift": "swift", ".rb": "ruby", ".php": "php",
            ".yml": "yaml", ".yaml": "yaml", ".md": "markdown"}.get(ext, "")


def _emit_tool_block(window, tool: str, args: dict, result: str, ok: bool) -> None:
    """Push a structured tool-call block to the web terminal (no-op elsewhere)."""
    fn = getattr(window, "tool_block", None)
    if not callable(fn):
        return
    path = str(args.get("path") or args.get("dir") or "")
    try:
        if tool == "run":
            fn({"kind": "bash", "header": f"Bash({str(args.get('command',''))[:80]})",
                "body": result[:2000], "lang": "bash", "ok": ok})
        elif tool == "edit_file":
            fn({"kind": "edit", "header": f"Edit({path})",
                "diff": {"old": str(args.get("old", ""))[:1500],
                         "new": str(args.get("new", ""))[:1500]},
                "lang": _lang_for(path), "ok": ok, "note": result[:80]})
        elif tool == "write_file":
            fn({"kind": "write", "header": f"Write({path})",
                "body": str(args.get("content", ""))[:2000],
                "lang": _lang_for(path), "ok": ok, "note": result[:80]})
        elif tool == "read_file":
            fn({"kind": "read", "header": f"Read({path})",
                "body": result[:1800], "lang": _lang_for(path), "ok": ok})
        elif tool in ("list_dir", "search"):
            fn({"kind": tool, "header": f"{'List' if tool=='list_dir' else 'Search'}"
                f"({path or args.get('pattern','')})",
                "body": result[:1500], "lang": "", "ok": ok})
    except Exception:
        log.exception("tool_block emit failed for %s", tool)   # was silently swallowed


def run_agent(eng, task: str, hints: list[str] | None = None) -> None:
    """Execute the agent loop (call from a worker thread)."""
    import activity
    import error_fixer

    window = eng.window
    messages = [{"role": "system", "content": _SYSTEM.replace("{steps}", str(_MAX_STEPS))},
                {"role": "user", "content":
                 f"TASK:\n{task[:2500]}\n\n"
                 f"KNOWN LOCATIONS (use these — do NOT guess paths):\n"
                 f"{_known_locations()}\n\n"
                 f"Start by listing/reading the relevant known location above." +
                 (f"\n\nSUGGESTED FIX COMMANDS (verify before using):\n" +
                  "\n".join(hints) if hints else "")}]

    code_mode = bool(getattr(eng, "_code_mode", False))
    eng._agent_running = True         # so a new /codemode message won't spawn a rival task
    activity.set_group("Coding agent")
    edits_made, verified = 0, False
    bad_replies = 0
    try:
        for step in range(1, _MAX_STEPS + 1):
            action = _llm(messages)
            if not action or "tool" not in action:
                # Don't give up on one malformed reply — nudge the model back
                # onto the JSON rails and retry a couple of times first.
                bad_replies += 1
                if bad_replies <= 3:
                    messages.append({"role": "user", "content":
                        "Your last reply was not a valid action. Respond with "
                        "EXACTLY ONE JSON object and nothing else, e.g. "
                        '{"tool":"read_file","args":{"path":"..."},"why":"..."} '
                        'or {"tool":"done","args":{"summary":"...","success":true}}.'})
                    continue
                window.add_chat_message(
                    "I couldn't get a clean next step from the model after a few "
                    "tries. Tell me to continue and I'll pick up where I left off.",
                    "gil")
                return
            bad_replies = 0
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
                if not code_mode:            # code mode is silent (typed surface)
                    window.set_state("speaking", said=spoken)
                    eng._speak(spoken)
                eng._last_spoke_at[0] = time.time() - 1.5
                eng._last_said[0] = spoken
                return

            # In /codemode, narrate the reasoning as a terminal bullet line
            # (Claude-Code style: a sentence, then the tool line beneath it).
            if code_mode and " " in why and why.lower() != tool:
                window.add_chat_message(why, "gil")

            # ---- self-review edits BEFORE the user ever sees them ----
            # GIL syntax-checks the proposed result and rejects no-op/broken
            # edits himself, re-planning instead of asking you to approve junk.
            if tool in ("edit_file", "write_file"):
                reject = _precheck_change(tool, args)
                if reject:
                    _p = Path(str(args.get("path", ""))).name or "the file"
                    activity.instant("code", "Rejected my own edit", reject[:90],
                                     status="fail")
                    window.add_chat_message(
                        f"I caught a problem with my own edit to `{_p}` — {reject}. "
                        "Re-planning instead of applying it.", "gil")
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content":
                        f"RESULT:\nSELF-CHECK REJECTED your {tool}: {reject}. "
                        "Do NOT repeat that same change. Read the file if you need to, "
                        "then propose a correct, meaningful edit — or move on."})
                    continue

            # ---- execute the tool ----
            needs_approval = False
            desc, appr_diff, appr_file = "", None, None
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
                appr_file = str(args.get("path", "?"))
                appr_diff = {"old": str(args.get("old", ""))[:1600],
                             "new": str(args.get("new", ""))[:1600]}
            elif tool == "write_file":
                needs_approval = True
                appr_file = str(args.get("path", "?"))
                appr_diff = {"old": "", "new": str(args.get("content", ""))[:1600]}

            if needs_approval:
                aid = activity.start("code", f"Waiting for approval: {why}")
                approved = _ask_approval(eng, f"*{why}*" + (f"\n\n{desc}" if desc else ""),
                                         diff=appr_diff, file=appr_file)
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
                        result = _verify_or_revert(args.get("path", ""), result)
                        if result.startswith("OK"):
                            edits_made += 1
                elif tool == "write_file":
                    result = _t_write_file(args)
                    if result.startswith("OK"):
                        result = _verify_or_revert(args.get("path", ""), result)
                        if result.startswith("OK"):
                            edits_made += 1
                else:
                    result = f"ERROR: unknown tool {tool!r}"
            except Exception as exc:
                result = f"ERROR: {exc}"

            ok = not result.startswith("ERROR")
            (activity.done if ok else activity.fail)(aid, result[:110])

            # Rich Claude-Code-style tool block for the /codemode terminal:
            # header + output/diff + language, so the UI can colour it.
            _emit_tool_block(window, tool, args, result, ok)

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
        eng._agent_running = False
        try:
            from ears import unmute
            unmute()
        except Exception:
            pass
        if not code_mode:          # don't drop a silent code session back into listen
            window.set_state("listening")


def run_async(eng, task: str, hints: list[str] | None = None) -> None:
    threading.Thread(target=run_agent, args=(eng, task, hints),
                     daemon=True, name="GIL-CodeAgent").start()
