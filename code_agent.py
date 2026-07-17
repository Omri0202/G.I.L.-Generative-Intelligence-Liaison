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
list_dir   {"path": "..."}                              list a folder (find real filenames)
search     {"pattern": "...", "dir": "..."}             find TEXT INSIDE files (recursive, regex)
run        {"command": "..."}                           PowerShell command
edit_file  {"path": "...", "old": "...", "new": "..."}  replace EXACT text (must match once)
write_file {"path": "...", "content": "..."}            create or overwrite a file
done       {"summary": "...", "success": true/false}    finish the task

FILENAMES — do not invent them:
- ALWAYS list_dir the folder first and use the EXACT names it returns. Never
  read/py_compile a path you have not seen in a listing (no "gil_eyes.py" guesses).
- `search` finds text INSIDE files — it does NOT find a file by name. To locate a
  file, list_dir. If a read/list fails, the error suggests the real name — use it.

METHOD (how a good engineer works):
1. UNDERSTAND — use the toolkit: list_dir for real names, run tests/py_compile,
   search for the pattern, read the actual code. Confirm every claim against the
   real code, never a guess.
2. If the task only asks you to LOOK / FIND / REVIEW / DIAGNOSE, STOP after
   understanding and report a findings list with done + success:TRUE (a report IS
   a success). Do NOT change anything. Don't dump raw search hits — for each real
   issue give file:line, why it matters, and the fix. A broad except that logs is
   NOT an issue.
3. If asked to CHANGE, make the smallest correct edit, then VERIFY it (pytest /
   py_compile) and read the result before you finish.

RULES:
- INVESTIGATE first: read the exact code (read_file/search) before editing.
  Never edit a file you have not read this session.
- Smallest change that actually fixes the problem. Match the existing style.
- Every edit MUST change real behaviour. NEVER propose cosmetic or no-op edits
  (e.g. adding an unused `except X as e`, re-quoting, or reformatting only).
- `old` in an edit MUST include enough surrounding lines to be BOTH unique and
  self-explanatory — never match a bare value like `false`, `0`, `True` or `}`;
  include the key/line it belongs to (e.g. `"voice_enabled": false`).
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


# The "smart" tier GIL reasons with. Change `agent_model` in data/gil_config.json
# to try a stronger free Groq model WITHOUT a rebuild — e.g.
#   "moonshotai/kimi-k2-instruct"   (strong at agentic coding)
#   "openai/gpt-oss-120b"           (strong general reasoning)
#   "qwen/qwen3-32b"                (solid, fast)
# It's always tried FIRST and falls back to the proven Llama models on any error.
_FALLBACK_MODELS = ("llama-3.3-70b-versatile", "llama-3.1-8b-instant")


def _agent_models() -> tuple[str, ...]:
    """Model try-order: the user's configured model first, then safe fallbacks."""
    try:
        cfg = json.loads((Path(__file__).parent / "data" / "gil_config.json")
                         .read_text(encoding="utf-8"))
        pick = str(cfg.get("agent_model", "")).strip()
    except Exception:
        pick = ""
    order = ([pick] if pick else []) + list(_FALLBACK_MODELS)
    seen, out = set(), []
    for m in order:
        if m and m not in seen:
            seen.add(m); out.append(m)
    return tuple(out)


def _extract_json(raw: str, must_have: str = "tool") -> dict | None:
    """Pull one JSON object out of a model reply, tolerantly. Handles ```json
    fences, leading prose, and nested braces by scanning for the first brace-
    balanced span that parses and contains `must_have`."""
    if not raw:
        return None
    txt = raw.strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt)
    if fence:
        txt = fence.group(1).strip()
    try:
        obj = json.loads(txt)
        if isinstance(obj, dict) and must_have in obj:
            return obj
    except Exception:
        pass
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
                        if isinstance(obj, dict) and must_have in obj:
                            return obj
                    except Exception:
                        pass
                    break
    return None


def _llm_json(messages: list, must_have: str = "tool",
              models: tuple[str, ...] | None = None,
              temperature: float = 0.2) -> dict | None:
    """One structured-JSON call to Groq with model + key fallback. Returns the
    first reply that parses to a dict containing `must_have`, else None."""
    payload = {"messages": messages, "temperature": temperature,
               "max_tokens": 1600, "response_format": {"type": "json_object"}}
    for model in (models or _agent_models()):
        payload["model"] = model
        for key in _keys():
            try:
                r = requests.post(_GROQ_URL, json=payload,
                                  headers={"Authorization": f"Bearer {key}",
                                           "Content-Type": "application/json"},
                                  timeout=45)
                if r.status_code == 429:
                    continue
                # json_object mode (or an unknown model) can 400 — retry plainly
                if r.status_code == 400 and "response_format" in payload:
                    p2 = {k: v for k, v in payload.items() if k != "response_format"}
                    r = requests.post(_GROQ_URL, json=p2,
                                      headers={"Authorization": f"Bearer {key}",
                                               "Content-Type": "application/json"},
                                      timeout=45)
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                obj = _extract_json(raw, must_have)
                if obj:
                    return obj
                log.debug("agent llm: no '%s' in reply: %.160s", must_have, raw)
            except Exception as exc:
                log.debug("agent llm attempt failed (%s): %s", model, exc)
    return None


def _extract_action(raw: str) -> dict | None:
    return _extract_json(raw, "tool")


def _llm(messages: list) -> dict | None:
    return _llm_json(messages, must_have="tool")


def _is_free_command(cmd: str) -> bool:
    low = cmd.lower().strip()
    return any(low.startswith(p) or f"| {p}" not in low and low == p
               for p in _FREE_RUN) or any(low.startswith(p) for p in _FREE_RUN)


# ── Tool implementations ──────────────────────────────────────────────────────

_SEARCH_SKIP_DIRS = ("__pycache__", ".git", ".github", "node_modules", "dist",
                     "build", "venv", ".venv", ".pytest_cache", "agent_backups",
                     "_web_profile", ".claude")
_SEARCH_SKIP_EXT = (".exe", ".dll", ".pyd", ".zip", ".jpg", ".jpeg", ".png",
                    ".gif", ".db", ".ico", ".pyc", ".spec", ".bin", ".pdf",
                    ".log")   # logs are pure noise for a code search


def _did_you_mean(p: Path) -> str:
    """Suggest real filenames close to a guessed one — stops the model from
    chasing a file it invented (e.g. gil_eyes.py → eyes.py)."""
    import difflib
    try:
        d = p.parent if str(p.parent) and p.parent.is_dir() else Path(__file__).parent
        names = [f.name for f in d.iterdir() if f.is_file()]
        near = difflib.get_close_matches(p.name, names, n=4, cutoff=0.45)
        return (" Did you mean: " + ", ".join(near) + "?") if near else ""
    except Exception:
        return ""


def _t_read_file(args: dict) -> str:
    p = Path(args.get("path", ""))
    if not p.exists():
        return f"ERROR: {p} does not exist.{_did_you_mean(p)}"
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
        return f"ERROR: {p} is not a folder.{_did_you_mean(p)}"
    items = []
    for c in sorted(p.iterdir())[:120]:
        items.append(f"{'[D]' if c.is_dir() else '   '} {c.name}")
    return "\n".join(items) or "(empty)"


def _t_search(args: dict) -> str:
    pattern = args.get("pattern", "")
    root = Path(args.get("dir", ".") or ".")
    if not pattern:
        return "ERROR: need a search pattern"
    if not root.is_dir():
        return f"ERROR: {root} is not a folder.{_did_you_mean(root)}"
    # Whole-file (multi-line) regex so patterns that span lines actually match —
    # the old line-by-line scan silently failed on those.
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.MULTILINE)

    def _skip(f: Path) -> bool:
        return (not f.is_file() or f.suffix.lower() in _SEARCH_SKIP_EXT
                or any(part in _SEARCH_SKIP_DIRS for part in f.parts))

    hits, total, DISPLAY, SCAN_CAP = [], 0, 40, 600
    for f in root.rglob("*"):
        if _skip(f):
            continue
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in rx.finditer(text):
            total += 1
            if len(hits) < DISPLAY:
                ln = text.count("\n", 0, m.start()) + 1
                snippet = text[m.start():m.start() + 160].splitlines()[0]
                hits.append(f"{f}:{ln}: {snippet.strip()[:140]}")
        if total >= SCAN_CAP:
            break
    if hits:
        more = (f"\n[{total}+ matches — showing first {len(hits)}; narrow the pattern "
                f"to see the rest]" if total > len(hits) else f"\n[{total} match(es)]")
        return "\n".join(hits) + more
    # No text matched — maybe they meant a FILE by that name (a common mixup).
    import difflib
    names = {}
    for f in root.rglob("*"):
        if f.is_file() and not any(part in _SEARCH_SKIP_DIRS for part in f.parts):
            names[f.name] = str(f)
    core = re.sub(r"[^\w.]", "", pattern)
    near = [names[n] for n in difflib.get_close_matches(core, list(names), n=6, cutoff=0.4)]
    near += [v for n, v in names.items() if core and core.lower() in n.lower() and v not in near]
    if near:
        return ("No text matches. NOTE: `search` looks INSIDE files. To open a file "
                "use read_file. Files whose NAME is similar:\n" + "\n".join(near[:8]))
    return "No matches."


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


# ── Reviewer agent — an independent second opinion before you approve ───────────

_REVIEW_SYSTEM = """You are a strict, independent senior code reviewer. You are \
shown the TASK, the CURRENT file, and a PROPOSED change to it. Judge ONLY whether \
the change is correct, on-task, and safe. Reply with ONE JSON object, nothing else:
{"verdict":"approve"|"warn"|"reject","reason":"<one short sentence>"}
- approve: it correctly advances the task and is safe.
- warn: it may work but has a real concern the user should see first.
- reject: it is clearly wrong, off-task, or dangerous.
Only choose "reject" when you are confident it is wrong. Keep the reason concrete."""


def _review_change(task: str, tool: str, args: dict) -> dict:
    """Second, fresh-context agent that critiques a proposed edit before the
    user sees it. Returns {"verdict","reason"}; never blocks on its own errors."""
    path = str(args.get("path", ""))
    p = Path(path)
    try:
        current = p.read_text(encoding="utf-8", errors="replace") if p.exists() else "(new file)"
    except Exception:
        current = "(unreadable)"
    if tool == "edit_file":
        change = (f"EDIT {path}\n--- remove ---\n{str(args.get('old',''))[:900]}\n"
                  f"--- insert ---\n{str(args.get('new',''))[:900]}")
    else:
        change = f"WRITE {path}\n--- new content ---\n{str(args.get('content',''))[:1400]}"
    msgs = [{"role": "system", "content": _REVIEW_SYSTEM},
            {"role": "user", "content":
             f"TASK:\n{task[:900]}\n\nCURRENT FILE ({p.name}):\n{current[:4500]}\n\n"
             f"PROPOSED CHANGE:\n{change}"}]
    out = _llm_json(msgs, must_have="verdict", temperature=0.0)
    if not out:
        return {"verdict": "approve", "reason": "(reviewer unavailable)"}
    v = str(out.get("verdict", "approve")).lower()
    if v not in ("approve", "warn", "reject"):
        v = "approve"
    return {"verdict": v, "reason": str(out.get("reason", ""))[:200]}


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


# ── Investigator's toolkit — the "basic commands" GIL adapts per task ───────────
# Concrete first moves in GIL's own tool vocabulary, so a smaller model still
# knows HOW to look before it touches anything. GIL adapts the patterns to the
# real subject of the task.
_PLAYBOOK = {
    "bugs": [
        'list_dir the project FIRST to get the REAL filenames — never guess a name',
        'run {"command":"python -m pytest -q"}  — do the tests already fail? read the failures first',
        r'search {"pattern":"except[^\n]*:\s*\n\s*(pass|continue)\s*$"}  — errors swallowed silently',
        r'search {"pattern":"==\s*None|!=\s*None"}  — should be `is` / `is not None`',
        r'search {"pattern":"def \w+\([^)]*=\s*(\[\]|\{\})"}  — mutable default argument',
        r'search {"pattern":"TODO|FIXME|HACK|XXX"}  — known-unfinished / risky spots',
        'A broad `except Exception` that LOGS or handles the error is FINE — do NOT '
        'report those; only a truly SILENT swallow (bare pass/continue) is a finding.',
    ],
    "error": [
        'search the EXACT error text / exception name, then read the file around each hit',
        'run {"command":"git log --oneline -15"}  and  git diff  — what changed recently',
        'run {"command":"python -m py_compile <file>"}  — confirm it imports/compiles',
        'reproduce it: run the smallest command that triggers it, then read the traceback',
    ],
    "refactor": [
        r'search {"pattern":"def \w+"}  — map the functions before touching them',
        'read the WHOLE file first; keep behaviour identical; change the smallest slice',
        'run {"command":"python -m pytest -q"} before AND after — the result must not change',
    ],
    "perf": [
        r'search {"pattern":"for .* in .*:\s*\n\s*for "}  — nested loops (hot spots)',
        'measure before optimising; change only speed, never behaviour',
        'run {"command":"python -m pytest -q"} after — behaviour must be identical',
    ],
    "tests": [
        'run {"command":"python -m pytest -q"}  — see the current state',
        r'search {"pattern":"def test_"}  — mirror the existing test style',
        'write tests that FAIL first, then confirm they pass after the fix',
    ],
}
_PLAYBOOK_KEYS = (
    ("error",    ("traceback", "exception", "stack trace", "error", "crash", "throws")),
    ("perf",     ("slow", "perf", "optimi", "speed", "faster", "lag")),
    ("tests",    ("test", "coverage", "pytest")),
    ("refactor", ("refactor", "clean up", "cleanup", "simplif", "tidy", "readab")),
    ("bugs",     ("bug", "wrong", "broken", "issue", "problem", "fix")),
)


def _task_mode(task: str) -> str:
    """'investigate' → report findings and change nothing; 'change' → edit (with
    approval). Looking/finding/reviewing without an action verb is read-only."""
    t = task.lower()
    look = ("look for", "look at", "look into", "find ", "what bug", "any bug",
            "are there", "review", "audit", "check for", "diagnose", "investigate",
            "what's wrong", "whats wrong", "analyse", "analyze", "report",
            "list the", "explore", "examine", "understand", "tell me", "show me",
            "how does", "what does", "what is", "whats in", "what's in")
    do = ("fix", "change", "add ", "implement", "refactor", "update", "make ",
          "create", "write", "rename", "remove", "delete", "enable", "disable",
          "set ", "install", "build ", "improve", "upgrade", "polish", "redesign")
    if any(w in t for w in look) and not any(w in t for w in do):
        return "investigate"
    return "change"


def _playbook_block(task: str) -> str:
    """Build the toolkit + working-mode text injected into the agent's context."""
    t = task.lower()
    key = "bugs"
    for k, words in _PLAYBOOK_KEYS:
        if any(w in t for w in words):
            key = k
            break
    moves = "\n".join("  • " + m for m in _PLAYBOOK[key])
    block = ("INVESTIGATOR'S TOOLKIT — proven first moves for this task (adapt the "
             "patterns to the ACTUAL subject; these are read-only and free):\n" + moves +
             "\nAlways OPEN and read the code before claiming anything — never trust a "
             "filename or a single search hit on its own.")
    if _task_mode(task) == "investigate":
        block += ("\n\nMODE — INVESTIGATE ONLY: do NOT edit or write any files. Use "
                  "read-only tools, then finish with `done` whose summary is a findings "
                  "LIST — for each item: file:line, why it's a problem, and the fix you'd "
                  "suggest. Change code only if the user explicitly asks you to fix it.")
    else:
        block += ("\n\nMODE — CHANGE: after investigating, make the smallest correct "
                  "change, then VERIFY with pytest/py_compile before `done`.")
    return block


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
                 f"{_playbook_block(task)}\n\n"
                 f"Start with the toolkit above on the relevant known location." +
                 (f"\n\nSUGGESTED FIX COMMANDS (verify before using):\n" +
                  "\n".join(hints) if hints else "")}]

    code_mode = bool(getattr(eng, "_code_mode", False))
    task_mode = _task_mode(task)      # 'investigate' (report only) vs 'change'
    eng._agent_running = True         # so a new /codemode message won't spawn a rival task
    if hasattr(window, "set_working"):
        window.set_working(True)      # pin the spinner + "working Ns" for the whole run
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
                # investigation tasks are READ-ONLY — never change files while the
                # user only asked to look/find/review (help, don't damage).
                if task_mode == "investigate":
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content":
                        "RESULT:\nThis is an INVESTIGATE-only task — do NOT edit files. "
                        "Keep gathering evidence with read-only tools, then finish with "
                        "`done` and report your findings as a list (file:line, the "
                        "problem, the suggested fix)."})
                    continue
                reject = _precheck_change(tool, args)
                if reject:
                    # Silent — the user never sees a change that failed a check.
                    activity.instant("code", "Self-check re-plan", reject[:90],
                                     status="fail")
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content":
                        f"RESULT:\nSELF-CHECK REJECTED your {tool}: {reject}. "
                        "Do NOT repeat that same change. Read the file if you need to, "
                        "then propose a correct, meaningful edit — or move on."})
                    continue

            # ---- Reviewer agent: an independent, fresh-context second opinion ----
            # Runs BEFORE the user is asked. If the change isn't a clean "approve",
            # GIL re-plans SILENTLY — the user only ever sees a change that passed
            # every automated check (the user has no way to judge code themselves).
            if tool in ("edit_file", "write_file"):
                rid = activity.start("brain", "Reviewing the change")
                verdict = _review_change(task, tool, args)
                v, reason = verdict["verdict"], verdict["reason"]
                activity.done(rid, f"{v}: {reason[:70]}")
                if v in ("reject", "warn"):
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content":
                        f"RESULT:\nAUTOMATED REVIEW did not pass this {tool} ({v}): {reason}. "
                        "Silently reconsider — re-read the code and propose a change that is "
                        "clearly correct and complete, or move on. Do not repeat this change."})
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
                approved = _ask_approval(
                    eng, f"*{why}*" + (f"\n\n{desc}" if desc else ""),
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
        if hasattr(window, "set_working"):
            window.set_working(False)   # spinner + timer stop only now, at the end
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
