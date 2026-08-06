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
import functools
import shutil
import re
import subprocess
import threading
import time
from pathlib import Path

import diff_hunks

import requests

from logger import get as _get_log

log = _get_log("agent")

_GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"
_MAX_STEPS = 20
_APPROVAL_TIMEOUT = 240   # seconds to wait for the user's yes/no
_CTX_BUDGET = 128000      # gpt-oss-120b usable context window (tokens) — gauge denominator

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
plan_next  {}                                           check off the current plan step, advance to the next
done       {"summary": "...", "success": true/false}    finish the task

WORK PLAN: you are given a numbered plan and told which step is your CURRENT
FOCUS. Do that step only. When it is genuinely satisfied, emit {"tool":"plan_next"}
to check it off and move on. When all steps are checked, emit `done` with your
report. Never repeat a step you already completed.

INTERNAL MONOLOGUE — reason privately BEFORE every action. This thinking is hidden
from the user; only your JSON action and final report are ever shown. Run it in
this order, briefly (do NOT skip straight to a tool call):
1. CHECKLIST — restate the goal as micro-steps: which real files to inspect, which
   symbols/variables are affected, what could break downstream.
2. SCOPE THE RISK — before a broad change, trace the ripple ("if I change this
   function's signature, what calls it?"). Before a big read, ask whether the
   payload risks the 8000-TPM Groq wall — if so, read a NARROW range (start/lines)
   or `search` a pattern instead of swallowing the whole file, and keep only the
   lines that matter.
3. DRAFT & PRE-VALIDATE — draft the exact regex / command / edit and stress-test it
   in your head: bracket balance, indentation, and that an edit's `old` matches
   exactly once and is never a bare token. (A bare `except: pass` you'd add is
   itself a bug — don't.)
4. SELF-CORRECT / PIVOT — if the draft fails your own check, state why and rewrite
   it BEFORE emitting the call. If you are about to repeat a tool call you already
   made with the same arguments, that path is a dead end: DROP it and pivot to a
   different investigation, or finish with `done` and a concrete report.

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
   issue give file:line, why it matters, and the fix.
   A finding must name a CONCRETE failure: specific input/state -> wrong output,
   crash, or resource leak. "could cause issues" / "may lead to problems" is not
   a finding — read the surrounding code and either state exactly what breaks or
   drop it. A broad except that logs is NOT an issue. `except OSError: break` (or
   similar) ending a while-loop that enumerates something (EnumKey, iterators,
   pagination) is the NORMAL termination idiom, not a bug — verify a catch is
   actually swallowing a real error before you report it, not just pattern-matching
   the word "except". Fewer, verified findings beat a padded list.
3. If asked to CHANGE, make the smallest correct edit, then VERIFY it (pytest /
   py_compile) and read the result before you finish.

LANGUAGE — {language}

DELIVERABLE MODE — when the task is to WRITE or CREATE prose content (essay, report,
treatise, document, presentation script, cover letter, study material) that needs NO
file inspection, do NOT use tools: go straight to `done` (success:true) with the
COMPLETE, ready-to-submit deliverable as the summary — nothing to read or run first.
{deliverable}

TRUTHFULNESS — never lie about what happened:
- NEVER claim a file was created / found / opened, a command ran, or a task is done
  unless a tool RESULT in THIS session confirms it. If a RESULT says "not found" /
  "File not found" / "ERROR" or otherwise fails, report that failure plainly — do
  NOT claim success or say you're "opening"/"done" when it failed.
- NEVER invent a file path, filename, or fact. Only cite paths/names a tool actually
  returned in a RESULT. If you don't know a path, run list_dir/search to find it.
- If you produced TEXT but did not save a file, say exactly that — never claim
  "attached" / "file created" / "הקובץ מוכן" / "מצורף".

REPORT FORMAT — your `done` summary IS the engineering report. Markdown. Be
THOROUGH and richly structured (a real report, never a one-liner). Write it like a
staff engineer updating a peer, never like a chatbot:
- OPEN with ONE sharp line: what you did and what you found ("I traced the run.
  **3 real problems**:"). NEVER "Sure", "Here is the report", "I hope this helps".
- SUMMARY TABLE (required whenever you report 2+ findings/results/items): render a
  Markdown table so it displays as a chart with columns and rows, e.g.
      | # | Finding | Location | Severity | Fix |
      |---|---------|----------|----------|-----|
      | 1 | Bare `except: pass` swallows the write error | `error_fixer.py:72` | high | log + re-raise |
  Bold the numbers, backtick every `file:line` and identifier. THEN expand below.
- Then NUMBERED findings, most important first, each shaped exactly:
  **1. Bold the first 3-6 words as the anchor** — then an em dash, then the fix
  in the same clause. Next line(s): 1-3 sentences on why it happens, exactly
  where (`file.py:line`), and the fix. Ground every claim in code you actually
  read or a command you actually ran — never a guess.
- Then, if there are smaller items: **Plus N quality fixes:** (bold, its own
  line) followed by `-` bullets, one punchy but informative line each, first
  few words bolded the same way.
- END with proof: the exact command you ran wrapped in backticks and its REAL
  result bolded ("`python -m pytest -q` — **120 passed**"), plus one line on
  what the user should do or run next.
- MICRO-TYPOGRAPHY (non-negotiable, this is what makes it scannable):
  * Bold EVERY number that matters: counts, line numbers, durations, before/after
    ("**256 matches**", "**3.4s → 4.6s**", "**120 tests**"). Use the arrow → for
    before/after pairs, always inside the bold.
  * Backtick EVERY code token, no exceptions: filenames (`gui.py`), paths
    (`dist/`), functions (`_speakable()`), exceptions (`OSError`), commands
    (`pytest -q`), regex, flags — anything that isn't prose.
  * A sub-heading is always **bold text**, its own short line, never a raw
    markdown `#` heading.
- Be QUANTITATIVE, not adjectival: real filenames, line numbers, counts, before ->
  after values — never "several issues" or "some problems" with no number.
- Never paste raw search output as the report. Turn hits into findings.

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


# ── Speed Mode / adaptive reasoning ───────────────────────────────────────────
# gpt-oss on Groq's free tier is capped at 8000 tokens-per-minute; heavy
# reasoning burns that budget and triggers backoff pauses. Speed Mode trades a
# little depth for a lot of wall-clock: medium reasoning + a tighter context so
# steps stay well under the cap and never 429/413.
#   deep → high effort, roomy context      (complex, careful debugging & edits)
#   fast → medium effort, trimmed context  (reads, structural sweeps, simple work)
#   auto → fast while investigating, deep the moment GIL starts changing code
# NOTE: Groq gpt-oss only accepts none/default/low/medium/high — there is no
# "max", so a "max"/"deep" preference maps to high (its true ceiling).
_SPEED_PROFILES = {
    "deep": {"effort": "high",   "max_tokens": 2200, "ctx": 16000},
    "fast": {"effort": "medium", "max_tokens": 1100, "ctx": 11000},
}
_EFFORTS = ("low", "medium", "high")


def _agent_speed_mode() -> str:
    """Resolve the Speed Mode preference: deep | fast | auto. Env wins (so it can
    be flipped without a rebuild), then gil_config.json, default 'auto'."""
    v = (os.getenv("GIL_SPEED_MODE") or "").strip().lower()
    if not v:
        try:
            cfg = json.loads((Path(__file__).parent / "data" / "gil_config.json")
                             .read_text(encoding="utf-8"))
            v = str(cfg.get("agent_speed_mode", "")).strip().lower()
        except Exception:
            v = ""
    if v in ("deep", "max", "quality", "smart"):
        return "deep"
    if v in ("fast", "speed", "quick"):
        return "fast"
    return "auto"


_policy_local = threading.local()   # per-thread approval override (Feature #10)
_cancel_local = threading.local()   # per-thread "should I stop?" probe


def set_thread_cancel_check(fn) -> None:
    """Install a callable this thread's long waits poll to abort early. Without
    it, Stop/Esc only lands between steps — i.e. after a 60s stream and a 30s
    rate-limit sleep — which reads as a dead button."""
    _cancel_local.check = fn


def _cancelled() -> bool:
    fn = getattr(_cancel_local, "check", None)
    if not fn:
        return False
    try:
        return bool(fn())
    except Exception:
        return False


def _interruptible_sleep(seconds: float, step: float = 0.25) -> bool:
    """Sleep in slices, bailing the moment a cancel lands. True if cancelled."""
    end = time.time() + max(0.0, seconds)
    while time.time() < end:
        if _cancelled():
            return True
        time.sleep(min(step, max(0.0, end - time.time())))
    return _cancelled()


def set_thread_policy(mode: str | None) -> None:
    """Force the approval policy for THIS thread only (background sub-agents)."""
    _policy_local.mode = (mode or "")


def _approval_policy() -> str:
    """Permission policy for DESTRUCTIVE ops (Feature #5). Reads (read/list/search)
    are always free; this only governs writes, edits, and non-safe shell commands:
      'ask'      — gate each one for the user's yes/no (default)
      'auto'     — auto-approve them (power mode; the safety blocklist + the
                   reviewer/precheck still run)
      'readonly' — refuse them entirely (investigate-only)
    A thread-local override wins first — a background sub-agent (Feature #10) must
    be able to run read-only WITHOUT flipping the policy for the main agent, which
    a process-global env var would do. Then env (GIL_APPROVAL_MODE), then
    gil_config.json (agent_approval_mode)."""
    v = (getattr(_policy_local, "mode", "") or "").strip().lower()
    if not v:
        v = (os.getenv("GIL_APPROVAL_MODE") or "").strip().lower()
    if not v:
        try:
            cfg = json.loads((Path(__file__).parent / "data" / "gil_config.json")
                             .read_text(encoding="utf-8"))
            v = str(cfg.get("agent_approval_mode", "")).strip().lower()
        except Exception:
            v = ""
    if v in ("auto", "yolo", "auto-approve", "trust"):
        return "auto"
    if v in ("readonly", "read-only", "plan", "none"):
        return "readonly"
    return "ask"


def _step_profile(mode: str, deep_phase: bool) -> dict:
    """Per-step reasoning profile. In auto mode we reason fast while investigating
    and switch to deep once real changes are on the table. An explicit
    GIL_REASONING=low|medium|high overrides just the effort (advanced/manual)."""
    resolved = ("deep" if deep_phase else "fast") if mode == "auto" else mode
    prof = dict(_SPEED_PROFILES.get(resolved, _SPEED_PROFILES["fast"]))
    forced = (os.getenv("GIL_REASONING") or "").strip().lower()
    if forced in _EFFORTS:
        prof["effort"] = forced
    return prof


# Deliverable detection + the manifesto are shared with the chat brain via
# deliverable.py — single source of truth, so the terminal and the web chat stay
# byte-for-byte in sync (zero prompt drift).
import deliverable as _deliverable
_is_content_task = _deliverable.is_content_task


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


def _retry_after_secs(r) -> float:
    """Seconds Groq wants us to wait before retrying a 429. Prefers the
    `retry-after` header, falls back to `x-ratelimit-reset-tokens`, else 0."""
    for hdr in ("retry-after", "x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"):
        v = r.headers.get(hdr, "")
        if not v:
            continue
        m = re.match(r"([\d.]+)\s*(ms|s|m)?", str(v).strip())
        if m:
            n = float(m.group(1))
            return n / 1000.0 if m.group(2) == "ms" else n * 60.0 if m.group(2) == "m" else n
    return 0.0


def _stream_groq(payload: dict, headers: dict, on_thought) -> tuple:
    """Streaming POST. Forwards gpt-oss `reasoning` deltas to on_thought the
    instant they arrive (the live typewriter thought stream) while accumulating
    the `content` deltas (the JSON action). Returns (status, content, response)."""
    r = requests.post(_GROQ_URL, json={**payload, "stream": True},
                      headers=headers, timeout=60, stream=True)
    if r.status_code != 200:
        return r.status_code, "", r
    parts: list[str] = []
    try:
        for line in r.iter_lines(decode_unicode=True):
            # Stop/Esc must land mid-stream, not 60s later at the next step.
            if _cancelled():
                break
            if not line or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                delta = json.loads(data)["choices"][0].get("delta", {})
            except Exception:
                continue
            think = delta.get("reasoning")
            if think and on_thought:
                try:
                    on_thought(think)
                except Exception:
                    pass
            if delta.get("content"):
                parts.append(delta["content"])
    finally:
        r.close()
    return 200, "".join(parts), r


def _call_groq(payload: dict, key: str, on_thought) -> tuple:
    """One Groq call normalised to (status, content_str, response). Streams the
    reasoning to on_thought when set and the model exposes it (gpt-oss); a plain
    blocking call otherwise. One return shape lets the sweep treat 429/413/400
    identically for both paths."""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if on_thought and "gpt-oss" in payload.get("model", ""):
        return _stream_groq(payload, headers, on_thought)
    r = requests.post(_GROQ_URL, json=payload, headers=headers, timeout=45)
    if r.status_code != 200:
        return r.status_code, "", r
    try:
        return 200, r.json()["choices"][0]["message"]["content"], r
    except Exception:
        return 200, "", r


def _llm_json(messages: list, must_have: str = "tool",
              models: tuple[str, ...] | None = None,
              temperature: float = 0.2, on_thought=None,
              effort: str = "high", max_tokens: int = 2000) -> dict | None:
    """One structured-JSON call to Groq with model + key fallback. Returns the
    first reply that parses to a dict containing `must_have`, else None.

    Rate limits are NOT the same as a bad reply: gpt-oss-120b has a low
    tokens-per-minute ceiling, so as the agent's context grows a step can 429
    on every model. When a whole sweep fails *only* to rate-limiting we wait out
    the reset window and retry the sweep, instead of returning None and letting
    the caller misread throttling as malformed JSON (which made runs bail early)."""
    base = {"messages": messages, "temperature": temperature,
            "response_format": {"type": "json_object"}}
    model_list = models or _agent_models()
    shrink_budget = 16000

    for sweep in range(4):
        rate_limited = too_big = False
        retry_after = 0.0
        for model in model_list:
            payload = dict(base)
            payload["model"] = model
            # Code mode is "slower but smarter": let gpt-oss reason hard. Keep its
            # max_tokens modest — Groq reserves prompt+max_tokens against the TPM
            # limit, so an oversized reservation makes big-context steps fail that
            # would otherwise fit. Only gpt-oss accepts reasoning_effort; strip it
            # for the Llama fallbacks (which would 400 on an unknown field).
            if "gpt-oss" in model:
                payload["reasoning_effort"] = effort
                payload["max_tokens"] = max_tokens
            else:
                payload.pop("reasoning_effort", None)
                payload["max_tokens"] = 1600
            # Only the primary gpt-oss attempt streams its reasoning to the
            # terminal; the Llama fallbacks have no reasoning to show.
            think_cb = on_thought if "gpt-oss" in model else None
            for key in _keys():
                try:
                    status, raw, r = _call_groq(payload, key, think_cb)
                    # 429 = rolling per-minute budget spent (wait it out).
                    if status == 429:
                        rate_limited = True
                        retry_after = max(retry_after, _retry_after_secs(r))
                        continue
                    # 413 = this single request exceeds the model's TPM ceiling
                    # (gpt-oss's is only 8k). A bigger-limit model may still take
                    # it; if none does, we shrink the transcript below and retry.
                    if status == 413:
                        too_big = True
                        continue
                    # json_object mode (or an unknown model) can 400 — retry plainly
                    if status == 400 and "response_format" in payload:
                        p2 = {k: v for k, v in payload.items() if k != "response_format"}
                        status, raw, r = _call_groq(p2, key, think_cb)
                    if status != 200:
                        continue
                    obj = _extract_json(raw, must_have)
                    if obj:
                        return obj
                    log.debug("agent llm: no '%s' in reply: %.160s", must_have, raw)
                except Exception as exc:
                    log.debug("agent llm attempt failed (%s): %s", model, exc)

        # A full sweep produced nothing. Distinguish *capacity* failures (the
        # models never ran) from genuine bad replies — only the latter should
        # fall through to None so the caller nudges the model's JSON.
        if too_big and sweep < 3:
            # Every model that could serve this was too small for the request.
            # Shrink the running transcript and retry immediately.
            shrink_budget = int(shrink_budget * 0.6)
            _cap_context(messages, max_chars=shrink_budget)
            log.warning("agent llm: request too large — trimmed to ~%d chars, retrying", shrink_budget)
            continue
        if rate_limited and sweep < 3:
            wait = min(max(retry_after, 6.0), 30.0)
            log.warning("agent llm: all models rate-limited — waiting %.1fs then retrying", wait)
            if _interruptible_sleep(wait):    # Stop pressed during the backoff
                return None
            continue
        break
    return None


def _trim_biggest_result(messages: list, tail_n: int = 6, min_len: int = 1200) -> bool:
    """Find the largest RESULT message in the last `tail_n` entries and shrink it
    to a short note. A dense tool dump (a huge minified file, a giant search hit
    list) is the most common reason a small model starts replying with garbage —
    this gives the agent one clean shot at recovering instead of just bailing.
    Mutates `messages` in place; returns whether anything was trimmed."""
    tail = messages[-tail_n:]
    biggest = max(
        (m for m in tail if m.get("role") == "user"
         and str(m.get("content", "")).startswith("RESULT:") and len(m["content"]) > min_len),
        key=lambda m: len(m["content"]), default=None)
    if not biggest:
        return False
    biggest["content"] = (biggest["content"][:300] +
        "\n…[trimmed — that result was too dense; use a narrower "
        "read_file range or search pattern next]")
    return True


def _cap_context(messages: list, max_chars: int = 16000) -> None:
    """Keep the running transcript small enough to fit gpt-oss-120b's tight
    tokens-per-minute ceiling (~8k TPM; Groq reserves prompt+max_tokens against
    it). Preserves the system prompt (index 0) and the original task (index 1),
    then drops the oldest intermediate turns, and finally shrinks any remaining
    oversized RESULT bodies, until the whole request fits. Mutates in place.

    ~16k chars ≈ 4–4.8k tokens, which with the 2k-token reply reservation stays
    under the 8k limit. Without this, long runs exceed even the Llama fallback's
    12k limit and every model rejects the request (429/413)."""
    def total() -> int:
        return sum(len(m.get("content", "")) for m in messages)
    # Drop oldest middle turns first (keep system + task at 0,1 and recent turns).
    while total() > max_chars and len(messages) > 8:
        del messages[2]
    # Still too big: shrink the largest remaining bodies.
    #
    # Every pass here MUST strictly reduce a body, or this loop never ends. The
    # old version truncated to [:400] and then appended a 28-char marker, giving
    # 428 chars — still over the 400 threshold, so the same message was picked
    # and "trimmed" to 428 again forever. It could only escape via total() <=
    # max_chars, but messages[0:2] (system + task) are never trimmed, so whenever
    # those two alone exceeded the cap the agent spun at 100% CPU until it was
    # killed. That was the real cause of `/goal` hanging for 15+ minutes.
    _MARK = "\n…[trimmed to save context]"
    _FLOOR = 400
    while total() > max_chars:
        biggest = max((m for m in messages[2:] if len(m.get("content", "")) > _FLOOR),
                      key=lambda m: len(m["content"]), default=None)
        if biggest is None:
            break
        before = len(biggest["content"])
        # keep the marker INSIDE the floor so the result is <= _FLOOR and the
        # message stops being a candidate on the next pass
        trimmed = biggest["content"][:max(0, _FLOOR - len(_MARK))] + _MARK
        if len(trimmed) >= before:
            break          # can't shrink any further — system+task exceed the cap
        biggest["content"] = trimmed


def _extract_action(raw: str) -> dict | None:
    return _extract_json(raw, "tool")


def _llm(messages: list, on_thought=None,
         effort: str = "high", max_tokens: int = 2000) -> dict | None:
    return _llm_json(messages, must_have="tool", on_thought=on_thought,
                     effort=effort, max_tokens=max_tokens)


def _speakable(md: str, limit: int = 160) -> str:
    """First sentence of a markdown report, stripped for TTS — the report is now
    formatted markdown, and reading '**1.' aloud sounds broken."""
    txt = re.sub(r"```[\s\S]*?```", " ", md or "")          # drop code fences
    txt = re.sub(r"^\s*[-*#>]+\s*", " ", txt, flags=re.MULTILINE)
    txt = re.sub(r"[*`_#>|]+", "", txt)                      # markdown punctuation
    txt = re.sub(r"^\s*\d+\.\s*", " ", txt, flags=re.MULTILINE)
    txt = re.sub(r"\s+", " ", txt).strip()
    if not txt:
        return "Task finished."
    first = re.split(r"(?<=[.!?])\s", txt)[0]
    return (first[:limit].rstrip(" ,;:—-") or txt[:limit]) + ("" if first.endswith((".", "!", "?")) else ".")


def _is_free_command(cmd: str) -> bool:
    low = cmd.lower().strip()
    return any(low.startswith(p) or f"| {p}" not in low and low == p
               for p in _FREE_RUN) or any(low.startswith(p) for p in _FREE_RUN)


# ── Tool implementations ──────────────────────────────────────────────────────

_SEARCH_SKIP_DIRS = ("__pycache__", ".git", ".github", "node_modules", "dist",
                     "build", "venv", ".venv", ".pytest_cache", "agent_backups",
                     "_web_profile", ".claude",
                     # Runtime state / browser profiles / generated assets. These
                     # hold thousands of files and no source: searching the project
                     # root spent 51s and 17s inside them on a single /goal run.
                     "whatsapp_profile", "generated_images", "3d_projects",
                     "face_db", "learning_projects", "screenshots", "site-packages")
_SEARCH_SKIP_EXT = (".exe", ".dll", ".pyd", ".zip", ".jpg", ".jpeg", ".png",
                    ".gif", ".db", ".ico", ".pyc", ".spec", ".bin", ".pdf",
                    ".log")   # logs are pure noise for a code search


# ── @file mentioning & context pinning (Feature #6) ─────────────────────────────
_file_cache: dict = {"root": None, "files": [], "ts": 0.0}
# Runtime-state / browser-profile / generated-asset dirs that are pure noise in a
# "which source file do you mean?" picker (on top of _SEARCH_SKIP_DIRS).
_MENTION_SKIP_DIRS = _SEARCH_SKIP_DIRS + (
    "whatsapp_profile", "generated_images", "3d_projects", "face_db",
    "learning_projects", "_web_profile", "site-packages", "models", "tmp")


def _all_project_files(base: Path) -> list[str]:
    """All project-relative file paths under `base` (cached ~20s per root), with
    vcs/build/binary/runtime noise skipped — the corpus for the @file autocomplete.
    Dotfiles are excluded so secrets like .env can never be listed or pinned."""
    now = time.time()
    if _file_cache["root"] == str(base) and now - _file_cache["ts"] < 20:
        return _file_cache["files"]
    files: list[str] = []
    try:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if d not in _MENTION_SKIP_DIRS and not d.startswith(".")]
            rel_dir = os.path.relpath(dirpath, base)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
            if depth > 8:
                dirnames[:] = []
                continue
            for fn in filenames:
                if fn.startswith("."):               # never list .env / dotfiles
                    continue
                if os.path.splitext(fn)[1].lower() in _SEARCH_SKIP_EXT:
                    continue
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                files.append(rel.replace("\\", "/"))
                if len(files) > 6000:                # sanity cap on huge trees
                    break
            if len(files) > 6000:
                break
    except Exception:
        log.debug("file walk failed for %s", base, exc_info=True)
    _file_cache.update(root=str(base), files=files, ts=now)
    return files


def list_project_files(query: str = "", limit: int = 40, root=None) -> list[str]:
    """Up to `limit` project files matching `query`, ranked name-first then by a
    subsequence fallback. Powers the @file autocomplete."""
    base = Path(root) if root else Path(os.getcwd())
    q = (query or "").lower().strip()
    scored = []
    for rel in _all_project_files(base):
        name, low = rel.rsplit("/", 1)[-1].lower(), rel.lower()
        if not q:
            rank = 0
        elif name == q:
            rank = 0
        elif name.startswith(q):
            rank = 1
        elif q in name:
            rank = 2
        elif q in low:
            rank = 3
        else:
            it = iter(low)                            # subsequence match
            if all(c in it for c in q):
                rank = 4
            else:
                continue
        scored.append(((rank, len(rel), rel), rel))
    scored.sort(key=lambda x: x[0])
    return [rel for _, rel in scored[:limit]]


def read_pinned_context(paths, cap_each: int = 6000, cap_total: int = 18000,
                        root=None) -> str:
    """Format pinned files as a context block for the model (Feature #6). Reads
    project-relative or absolute paths; truncates per-file and overall so a fat
    pin can't blow the token budget."""
    if not paths:
        return ""
    base = Path(root) if root else Path(os.getcwd())
    blocks, total = [], 0
    for raw in paths:
        rel = str(raw).strip()
        if not rel:
            continue
        p = Path(rel)
        if p.name.startswith("."):          # never expose .env / dotfiles
            continue
        if not p.is_absolute():
            p = base / rel
        try:
            body = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            blocks.append(f"### {rel}\n(could not read this file)")
            continue
        if len(body) > cap_each:
            body = body[:cap_each] + "\n… [truncated]"
        if total + len(body) > cap_total:
            blocks.append(f"### {rel}\n(omitted — pinned context budget reached)")
            break
        total += len(body)
        ext = p.suffix.lstrip(".") or ""
        blocks.append(f"### {rel}\n```{ext}\n{body}\n```")
    if not blocks:
        return ""
    return ("[PINNED CONTEXT — the user attached these files; use them as the "
            "primary reference]\n\n" + "\n\n".join(blocks))


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
    # A single dense/minified line (bundled JS, one-line CSS) can be tens of
    # thousands of chars — that poisoned a small model's context and triggered
    # repeated "couldn't get a clean next step" bails. Cap each line so the
    # slice always has real line structure, however the source file is written.
    LINE_CAP = 300
    def _clip(l: str) -> str:
        return l if len(l) <= LINE_CAP else l[:LINE_CAP] + f"…[+{len(l)-LINE_CAP} chars]"
    body = "\n".join(f"{i}: {_clip(l)}" for i, l in enumerate(chunk, start))
    return f"[{p} — {len(lines)} lines total, showing {start}-{start+len(chunk)-1}]\n{body}"


def _t_list_dir(args: dict) -> str:
    p = Path(args.get("path", "."))
    if not p.is_dir():
        return f"ERROR: {p} is not a folder.{_did_you_mean(p)}"
    items = []
    for c in sorted(p.iterdir())[:120]:
        items.append(f"{'[D]' if c.is_dir() else '   '} {c.name}")
    return "\n".join(items) or "(empty)"


_SELF_TOOLING_RE = re.compile(r'^"[\w]+":\s*\("[a-z_]+",')   # activity-label tuple


def _is_search_tooling(line: str) -> bool:
    """True if this source line DEFINES one of GIL's own bug-hunt searches or an
    activity label, rather than containing a real occurrence — so searching GIL's
    own project doesn't report its own machinery. Matches:
      • investigator-playbook search definitions  ( ...{"pattern":"TODO|..."}... )
      • inline `search {...}` playbook examples
      • activity registry labels  ( "find_todos": ("code", "Finding TODOs") )
    """
    s = line.strip()
    return ('"pattern":' in s or "search {" in s
            or bool(_SELF_TOOLING_RE.match(s)))


#: no source file is this big; anything larger is generated or binary-ish and
#: reading it is pure cost. Without this cap a search read 95MB per call.
_SEARCH_MAX_BYTES = 2_000_000


def _iter_source_files(root: Path):
    """Every file worth searching, pruning skipped folders as we go.

    os.walk with in-place dirnames pruning, NOT rglob. rglob descends into
    every directory and lets the caller reject the results afterwards, so a
    search still walked .git, node_modules, the browser profile and the backup
    folder in full: 10,500 paths visited to keep 672 of them."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SEARCH_SKIP_DIRS]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in _SEARCH_SKIP_EXT:
                continue
            p = os.path.join(dirpath, name)
            try:
                if os.path.getsize(p) > _SEARCH_MAX_BYTES:
                    continue
            except OSError:
                continue
            yield Path(p)


@functools.lru_cache(maxsize=1)
def _rg_path() -> str:
    """ripgrep, if installed. Cached — this sits on the hot path."""
    return shutil.which("rg") or ""


def _rg_search(pattern: str, root: Path, display: int, scan_cap: int):
    """Search with ripgrep. Returns (hits, total, filtered), or None if it
    can't run and the caller should fall back.

    The pure-Python path below opens and reads EVERY file in the tree on every
    single search — measured at 6.4s per call on this project. An agent runs
    many searches to answer one question, so that alone was most of "why is he
    taking so long". ripgrep does the same work in milliseconds, and it was
    already being detected and used by dev_search; the agent's own search tool
    was simply never taught about it."""
    rg = _rg_path()
    if not rg:
        return None
    cmd = [rg, "--no-heading", "--line-number", "--color", "never",
           "--no-messages", "--max-columns", "220", "-e", pattern]
    for d in _SEARCH_SKIP_DIRS:
        cmd += ["--glob", f"!{d}/**"]
    for e in _SEARCH_SKIP_EXT:
        cmd += ["--glob", f"!*{e}"]
    cmd += ["--", str(root)]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=20)
    except Exception:
        return None
    # 0 = matched, 1 = no match, anything else is a real failure (a regex rg
    # rejects, say) — fall back so the caller's re.escape retry still applies
    if p.returncode not in (0, 1):
        return None

    hits, total, filtered = [], 0, 0
    for raw in p.stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        path, ln, line = parts
        if _is_search_tooling(line):
            filtered += 1
            continue
        total += 1
        if len(hits) < display:
            hits.append(f"{path}:{ln}: {line.strip()[:140]}")
        if total >= scan_cap:
            break
    return hits, total, filtered


def _t_search(args: dict) -> str:
    pattern = args.get("pattern", "")
    root = Path(args.get("dir", ".") or ".")
    if not pattern:
        return "ERROR: need a search pattern"
    if not root.is_dir():
        return f"ERROR: {root} is not a folder.{_did_you_mean(root)}"

    fast = _rg_search(pattern, root, 40, 600)
    if fast is not None and fast[0]:
        hits, total, filtered = fast
        more = (f"\n[{total}+ matches — showing first {len(hits)}; narrow the "
                f"pattern to see the rest]" if total > len(hits)
                else f"\n[{total} match(es)]")
        if filtered:
            more += (f"\n[{filtered} match(es) skipped — GIL's own search "
                     "patterns / activity labels, not real occurrences]")
        return "\n".join(hits) + more
    # No ripgrep hits: fall through, both to cover the case where rg is absent
    # and to reuse the "did you mean a FILE by that name?" recovery below,
    # which answers a different question than "no text matched".

    # Whole-file (multi-line) regex so patterns that span lines actually match —
    # the old line-by-line scan silently failed on those.
    try:
        rx = re.compile(pattern, re.MULTILINE)
    except re.error:
        rx = re.compile(re.escape(pattern), re.MULTILINE)

    hits, total, filtered, DISPLAY, SCAN_CAP = [], 0, 0, 40, 600
    files = list(_iter_source_files(root))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for m in rx.finditer(text):
            ls = text.rfind("\n", 0, m.start()) + 1
            le = text.find("\n", m.start())
            line = text[ls:le if le != -1 else len(text)]
            # When GIL searches its OWN source, its bug-hunt patterns (TODO
            # markers, None-equality, the mutable-default regex) match the literal
            # strings that DEFINE those very patterns — the investigator playbook
            # and the activity labels. Reporting those as findings is the "hall of
            # mirrors" that produced 3 invented bugs. Drop GIL's own tooling lines.
            if _is_search_tooling(line):
                filtered += 1
                continue
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
        if filtered:
            more += (f"\n[{filtered} match(es) skipped — GIL's own search patterns / "
                     "activity labels, not real occurrences]")
        return "\n".join(hits) + more
    # No text matched — maybe they meant a FILE by that name (a common mixup).
    # Reuses the list already walked above; re-walking the tree here is why a
    # miss cost four times what a hit did.
    import difflib
    names = {f.name: str(f) for f in files}
    core = re.sub(r"[^\w.]", "", pattern)
    near = [names[n] for n in difflib.get_close_matches(core, list(names), n=6, cutoff=0.4)]
    near += [v for n, v in names.items() if core and core.lower() in n.lower() and v not in near]
    if near:
        return ("No text matches. NOTE: `search` looks INSIDE files. To open a file "
                "use read_file. Files whose NAME is similar:\n" + "\n".join(near[:8]))
    if filtered:
        return (f"No real matches. ({filtered} match(es) were GIL's own search "
                "patterns / activity labels — tooling, not findings. Do NOT report "
                "these as bugs.)")
    return "No matches."


_run_local = threading.local()   # per-thread subprocess time cap


def set_thread_run_timeout(seconds) -> None:
    """Cap how long ONE `run` command may take on this thread. An investigation
    gets a short cap so a single slow command (a full pytest suite is ~133s)
    cannot starve the run's wall-clock budget before it can converge."""
    _run_local.timeout = seconds


def _run_timeout() -> float:
    try:
        v = getattr(_run_local, "timeout", None)
        return float(v) if v else 240.0
    except (TypeError, ValueError):
        return 240.0


def _t_run(cmd: str) -> str:
    limit = _run_timeout()
    try:
        p = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", cmd],
            capture_output=True, text=True, timeout=limit,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        return f"[exit {p.returncode}]\n{out[-1800:]}" if out else f"[exit {p.returncode}] (no output)"
    except subprocess.TimeoutExpired:
        # Tell the model plainly, so it picks a cheaper move instead of retrying
        # the same long command and burning the rest of the budget.
        return (f"ERROR: command exceeded the {int(limit)}s limit and was killed. "
                "Do NOT run it again — use a faster, narrower command (target one "
                "test file or one module), or answer from reading the code instead.")
    except Exception as exc:
        return f"ERROR: {exc}"


# ── Checkpoints — every file the agent touches is backed up first ─────────────

_BACKUP_ROOT = Path(__file__).parent / "data" / "agent_backups"
# Layered checkpoint model (Feature #8):
#   _session_backups   — the ACTIVE layer: files touched since the last checkpoint.
#   _checkpoint_stack  — sealed named restore points, oldest → newest.
#   _ckpt_gen          — generation counter so each layer's on-disk backups are
#                        uniquely named (a re-edit after a checkpoint must not
#                        clobber the earlier layer's backup).
_session_backups: dict[str, Path] = {}      # original path -> backup copy (active layer)
_checkpoint_stack: list[dict] = []          # [{"label": str, "files": {orig: backup}}]
_ckpt_gen = 0


def _checkpoint(p: Path) -> None:
    """Save the pre-edit version of a file (once per active layer per file)."""
    key = str(p.resolve())
    if key in _session_backups:
        return
    try:
        import shutil, hashlib
        _BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        tag = hashlib.md5(key.encode()).hexdigest()[:10]
        dest = _BACKUP_ROOT / f"g{_ckpt_gen}_{tag}_{p.name}"
        if p.exists():
            shutil.copy2(str(p), str(dest))
        else:
            dest = _BACKUP_ROOT / f"g{_ckpt_gen}_{tag}_{p.name}.NEW"   # marks a created file
            dest.write_text("", encoding="utf-8")
        _session_backups[key] = dest
    except Exception:
        log.debug("checkpoint failed for %s", p, exc_info=True)


def create_checkpoint(label: str = "") -> dict:
    """Seal the active change-layer as a named restore point and open a fresh one
    (Feature #8). Returns {label, files, total} — files in this point, total points."""
    global _ckpt_gen
    files = dict(_session_backups)
    label = (label or "").strip() or f"checkpoint {len(_checkpoint_stack) + 1}"
    _checkpoint_stack.append({"label": label, "files": files})
    _session_backups.clear()
    _ckpt_gen += 1
    return {"label": label, "files": len(files), "total": len(_checkpoint_stack)}


def _restore_layer(files: dict) -> tuple[int, int, int]:
    """Write each backed-up file back to disk (delete the ones we created)."""
    import shutil
    restored = removed = failed = 0
    for orig, backup in files.items():
        try:
            if str(backup).endswith(".NEW"):
                Path(orig).unlink(missing_ok=True)
                removed += 1
            else:
                shutil.copy2(str(backup), orig)
                restored += 1
        except Exception:
            failed += 1
    return restored, removed, failed


def _fmt_undo(restored: int, removed: int, failed: int, label: str | None) -> str:
    parts = []
    if restored:
        parts.append(f"restored {restored} file{'s' if restored != 1 else ''}")
    if removed:
        parts.append(f"deleted {removed} file{'s' if removed != 1 else ''} I had created")
    if not parts:
        return "Nothing to restore."
    head = "Undone" + (f" to “{label}”" if label else "")
    msg = f"{head} — " + " and ".join(parts) + "."
    if failed:
        msg += f" ({failed} failed — check the backups in data/agent_backups)"
    return msg


def checkpoint_status() -> dict:
    """Snapshot for the UI/commands: pending (uncommitted) edits + saved points."""
    return {"pending": len(_session_backups),
            "checkpoints": [c["label"] for c in _checkpoint_stack],
            "can_undo": bool(_session_backups or _checkpoint_stack)}


def undo_last_changes() -> str:
    """LIFO instant-undo (Feature #8): revert the most recent change-layer — the
    edits made since the last checkpoint, or, if there are none pending, the last
    saved checkpoint itself. Repeated calls walk further back."""
    global _ckpt_gen
    if _session_backups:
        files = dict(_session_backups)
        _session_backups.clear()
        label = None
    elif _checkpoint_stack:
        layer = _checkpoint_stack.pop()
        files, label = layer["files"], layer["label"]
        _ckpt_gen = max(0, _ckpt_gen - 1)
    else:
        return "Nothing to undo — I haven't changed any files this session."
    restored, removed, failed = _restore_layer(files)
    return _fmt_undo(restored, removed, failed, label)


def undo_all_changes() -> str:
    """Restore EVERY file the agent touched this session, back to session start —
    flattens all layers (oldest backup wins). The 'undo your changes' fast path."""
    global _ckpt_gen
    layers = [c["files"] for c in _checkpoint_stack] + [_session_backups]
    merged: dict[str, Path] = {}
    for files in layers:                        # oldest → newest
        for orig, backup in files.items():
            merged.setdefault(orig, backup)      # oldest-wins = session-start original
    if not merged:
        return "Nothing to undo — I haven't changed any files this session."
    restored, removed, failed = _restore_layer(merged)
    _session_backups.clear()
    _checkpoint_stack.clear()
    _ckpt_gen = 0
    return _fmt_undo(restored, removed, failed, None)


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
    if getattr(eng, "_agent_cancel", False):
        return False              # Esc/Stop fired while we were waiting → decline
    return gate["approved"]


def _edit_original_proposed(tool: str, args: dict):
    """For an edit_file / write_file action, return (original_text, proposed_text,
    Path) so the change can be diffed into hunks — or (None, None, None) if it
    can't be cleanly previewed (missing file, ambiguous edit target)."""
    try:
        p = Path(args.get("path", ""))
    except Exception:
        return None, None, None
    if not str(p):
        return None, None, None
    if tool == "write_file":
        original = p.read_text(encoding="utf-8") if p.exists() else ""
        return original, str(args.get("content", "")), p
    if tool == "edit_file":
        if not p.exists():
            return None, None, None
        try:
            original = p.read_text(encoding="utf-8")
        except Exception:
            return None, None, None
        old, new = args.get("old", ""), args.get("new", "")
        # only preview an unambiguous single-match replacement — otherwise let the
        # normal tool run and report the exact "not found / N matches" error.
        if not old or original.count(old) != 1:
            return None, None, None
        return original, original.replace(old, new, 1), p
    return None, None, None


def _ask_hunk_review(eng, file: str, hunks: list, why: str):
    """Post a hunk-by-hunk diff review and block until the user decides per hunk
    (Feature #3). Returns the decision map {id: {action, text}}, or None if the
    review was cancelled / timed out."""
    gate = {"event": threading.Event(), "decisions": None, "cancelled": False}
    eng._hunk_gate = gate
    try:
        eng.window.request_hunk_review({
            "file": file, "why": why,
            "summary": diff_hunks.summarize(hunks), "hunks": hunks})
    except Exception:
        eng._hunk_gate = None
        return None
    gate["event"].wait(_APPROVAL_TIMEOUT)
    eng._hunk_gate = None
    if getattr(eng, "_agent_cancel", False) or gate["cancelled"]:
        return None
    return gate["decisions"]


# ── Main loop ─────────────────────────────────────────────────────────────────

#: Surfaces that aren't Python modules and so have no docstring to harvest,
#: but own big pieces of GIL. The chat UI in particular is one 2,700-line file
#: holding all of its markup, styling and behaviour — the single most likely
#: place for a "change how the interface looks" task to belong.
_NON_PY_MAP = [
    ("web_chat/index.html", "THE ENTIRE CHAT UI — markup, CSS and JS in one "
                            "file. Anything about how the chat LOOKS or "
                            "behaves in the browser is here."),
    ("web_chat/scan.html", "the face/hand scan page"),
    ("data/gil_config.json", "user settings: model, voice, theme, modes"),
    ("data/token_usage.json", "recorded API token spend"),
]


@functools.lru_cache(maxsize=1)
def _module_blurbs() -> tuple:
    """(module, one-line purpose) for every top-level module, taken from the
    module's own docstring.

    Harvested rather than hand-written, because a hand-written index of a
    ~120-module project is out of date the week after it is written, and a
    stale map sends the agent somewhere confidently wrong."""
    out = []
    root = Path(__file__).parent
    for p in sorted(root.glob("*.py")):
        if p.name.startswith("_"):
            continue
        try:
            with p.open("r", encoding="utf-8", errors="ignore") as fh:
                head = fh.read(1400)
        except Exception:
            continue
        m = re.search(r'^\s*(?:"""|\'\'\')(.*?)(?:"""|\'\'\')', head, re.S)
        if not m:
            continue
        for line in m.group(1).splitlines():
            line = line.strip()
            # skip a bare "module.py —" title line and go for the sentence
            if not line or line.rstrip(" -—").lower() == p.stem.lower():
                continue
            line = re.sub(r'^' + re.escape(p.name) + r'\s*[—:-]\s*', '', line)
            if len(line) > 8:
                out.append((p.name, line[:150]))
                break
    return tuple(out)


#: Words that appear in half the docstrings in the project and so carry no
#: signal about WHERE something lives. Without this filter "make the usage ring
#: smaller" ranked weather.py first, purely because "make" is in its docstring —
#: the same wrong-place hunt this map exists to prevent.
_MAP_STOP = frozenset("""
the and for with that this from into your you its his out are was has have not
make makes making made change changes changed fix fixes fixed add adds added
new old more less small smaller big bigger better best good bad show shows
shown display displays use uses used using need needs should would could
project gil file files code line lines when what where which how why does
""".split())

#: Task words that mean "this is about the interface", which lives almost
#: entirely in one HTML file rather than in any module.
_UI_WORDS = frozenset("""
ui interface chat window ring button menu bar panel colour color theme dark
light font size smaller bigger layout css style styling screen page dropdown
popup icon avatar bubble scroll sidebar composer input textbox placeholder
""".split())


def _score_words(task: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", (task or "").lower())
            if w not in _MAP_STOP}


def _project_map(task: str = "", limit: int = 14) -> str:
    """The modules most likely to matter for THIS task, one line each.

    Ranked rather than dumped: the whole index is thousands of tokens, and
    burying the answer inside it is barely better than not having it. Without
    any map the agent had to grep blind for the file owning a feature, which is
    what made a one-line change take minutes."""
    words = _score_words(task)
    entries = [(n, b, n[:-3].lower()) for n, b in _module_blurbs()]
    entries += [(p, d, p.lower()) for p, d in _NON_PY_MAP]

    ui = bool(words & _UI_WORDS)
    scored = []
    for name, blurb, stem in entries:
        hay = stem + " " + blurb.lower()
        # a hit in the FILENAME is worth far more than one in prose: the name
        # is what the module is, the docstring is what it happens to mention
        score = sum(4 if w in stem else 1 for w in words if w in hay)
        if ui and name.endswith(".html"):
            score += 9          # the interface is one file; say so loudly
        # A data file is STATE, not logic. "token usage isn't being recorded"
        # is a question about the code that writes the file, not the file —
        # yet token_usage.json outscored token_meter.py on filename alone.
        if name.startswith("data/") and not (words & {"config", "setting",
                                                      "settings", "json"}):
            score -= 6
        if score > 0:
            scored.append((score, name, blurb))
    scored.sort(key=lambda t: (-t[0], t[1]))
    top = scored[:limit]
    if not top:
        return ("  (nothing matched by name — list_dir the project root, and "
                "remember the whole chat interface is web_chat/index.html)")
    return "\n".join(f"- {n} — {b}" for _, n, b in top)


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


# A message only reaches the coding agent because the user is in /codemode, where
# EVERY message is dispatched here. But "do research on whales" is not a coding
# task — the agent can only think in terms of THIS project, so it spent 15 minutes
# grepping the codebase for "whale". These signals let the caller route such a
# request to the normal brain (which does web research, answers questions, etc.)
# instead of forcing it through the code agent.
_CODE_SIGNALS = (
    ".py", ".js", ".ts", ".html", ".css", ".json", ".md ", ".txt", ".yml",
    " file", " files", " code", " codebase", " repo", " project", " function",
    " method", " class ", " module", " script", " variable", " import",
    " bug", " crash", " traceback", " exception", " stack trace", " error",
    " test", " pytest", " refactor", " implement", " compile", " syntax",
    " endpoint", " database", " query", " commit", " git ", " branch",
    " build ", " deploy", " debug", " gil", " agent", " brain", " def ",
    " runtime", " directory", " folder", " the path", " config", " feature",
)
_NONCODE_SIGNALS = (
    " research ", "research on", "research about", " look up ", " google ",
    "search the web", "search the internet", " what is ", " what are ",
    " what's ", " what was", " what were", " who is ", " who was", " who were",
    " when did", " when was", " where is", " where are", "tell me about",
    " info about", " info on", "give me info", " weather", " the news",
    "write a poem", "write a story", "write a song", "write me a poem",
    " translate ", " in hebrew", " in spanish", " in french", " recipe",
    "history of", "facts about", "information about", "meaning of", " capital of",
    # NOTE: deliberately NOT "why does" / "how do" / "how many" — in a developer
    # terminal those overwhelmingly introduce debugging questions ("why does the
    # stop button not work"), so they stay coding by default.
)


def _mostly_non_ascii(t: str) -> bool:
    """True if the message is largely non-Latin script (Hebrew, Arabic, Cyrillic,
    CJK…). Source code and dev requests are ASCII, so this is natural language —
    e.g. a bare 'בעברית' has no English signal and must not default to coding."""
    letters = [c for c in t if c.isalpha()]
    if not letters:
        return False
    non_ascii = sum(1 for c in letters if ord(c) > 127)
    return non_ascii / len(letters) > 0.4


def is_coding_task(task: str) -> bool:
    """True if this belongs to the coding agent (about code / files / this
    project); False for general research / knowledge / creative requests that
    only landed here because the user happens to be in /codemode.

    Code signals win ties: "research how the approval flow works in the code"
    stays a coding task, while "research whales" does not."""
    t = " " + (task or "").lower().strip() + " "
    if any(s in t for s in _CODE_SIGNALS):
        return True
    if _mostly_non_ascii(t):
        return False            # non-Latin natural language → the brain, not code
    if any(s in t for s in _NONCODE_SIGNALS):
        return False
    return True                 # ambiguous → they ARE in codemode, treat as code


def _task_mode(task: str) -> str:
    """'investigate' → report findings and change nothing; 'change' → edit (with
    approval). Looking/finding/reviewing without an action verb is read-only."""
    t = task.lower()
    look = ("look for", "look at", "look into", "find ", "what bug", "any bug",
            "are there", "review", "audit", "check ", "diagnose", "investigate",
            "what's wrong", "whats wrong", "analyse", "analyze", "report",
            "list the", "explore", "examine", "understand", "tell me", "show me",
            "how does", "what does", "what is", "whats in", "what's in")
    do = ("fix", "change", "add ", "implement", "refactor", "update", "make ",
          "create", "write", "rename", "remove", "delete", "enable", "disable",
          "set ", "install", "build ", "improve", "upgrade", "polish", "redesign")
    # "…for improvements" / "…for things to fix": here the action word is the
    # SUBJECT of a survey, not an imperative — the user is asking WHAT could be
    # improved, not ordering an edit. Drop those phrases before looking for a
    # command verb, otherwise "look at the gil project for improvements" scores
    # as 'change' and silently unlocks the long time budget, the 240s command
    # cap and the "verify with pytest" playbook — which is exactly how that task
    # ended up running the full suite twice and never reporting back.
    intent = re.sub(r"\bfor\s+(?:\w+\s+){0,3}"
                    r"(?:improv|enhanc|optimi|fix|better|clean|refactor|polish)\w*",
                    " ", t)
    if any(w in t for w in look) and not any(w in intent for w in do):
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
    investigating = _task_mode(task) == "investigate"
    moves_list = list(_PLAYBOOK[key])
    if investigating:
        # An exploratory pass must not run the suite. It costs ~133s here, teaches
        # a survey nothing that reading the code won't, and the agent used to run
        # it repeatedly — which is what actually exhausted the step budget.
        moves_list = [m for m in moves_list
                      if "pytest" not in m and "npm test" not in m]
    moves = "\n".join("  • " + m for m in moves_list)
    block = ("INVESTIGATOR'S TOOLKIT — proven first moves for this task (adapt the "
             "patterns to the ACTUAL subject; these are read-only and free):\n" + moves +
             "\nAlways OPEN and read the code before claiming anything — never trust a "
             "filename or a single search hit on its own.")
    if investigating:
        block += ("\n\nMODE — INVESTIGATE ONLY: do NOT edit or write any files. Use "
                  "read-only tools, then finish with `done` whose summary is a findings "
                  "LIST — for each item: file:line, why it's a problem, and the fix you'd "
                  "suggest. Change code only if the user explicitly asks you to fix it."
                  "\n\nYOU CHANGED NOTHING. Never write that you removed, fixed, changed, "
                  "cleaned up, refactored or improved anything, and NEVER include a "
                  "'Plus N quality fixes' section — that section is for changes actually "
                  "made, and you made none. Describe only what you FOUND and what you "
                  "WOULD change."
                  "\n\nA FINDING MUST BE A REAL DEFECT you read in actual logic. A keyword "
                  "that appears INSIDE a search pattern, a regex string, a `\"pattern\":` "
                  "value, or a UI/label string is GIL's OWN tooling — NOT a finding. If a "
                  "search result is a self-match of your own investigator patterns, ignore "
                  "it. Do not invent findings to have something to report — 'no issues "
                  "found' is a valid, honest result."
                  "\n\nDO NOT RUN THE TEST SUITE. `pytest`, `npm test` and other "
                  "whole-project runs take minutes and tell a survey nothing that "
                  "reading the code won't. Investigate with list_dir, search and "
                  "read_file. Run tests only if the user explicitly asked for test "
                  "results, or to verify a change you were asked to make.")
    else:
        block += ("\n\nMODE — CHANGE: after investigating, make the smallest correct "
                  "change, then VERIFY with pytest/py_compile before `done`.")
    return block


# ── Planner — turn a task into a short, ordered, executable work plan ───────────
_PLAN_SYSTEM = """\
You are the PLANNER for G.I.L.'s coding agent. Given a task, produce a SHORT,
concrete, ordered work plan that the agent will then execute ONE step at a time
with these tools: read_file, list_dir, search, run (PowerShell), edit_file,
write_file.

Return ONE JSON object only:  {"plan": ["step 1", "step 2", ...]}

What a GOOD plan looks like:
- 3 to 5 steps. Fewer is better. Each step is ONE concrete, checkable unit.
- Ordered the way a staff engineer works: UNDERSTAND (list_dir, run tests,
  search, read the real code) BEFORE deciding — then act — then VERIFY.
- Each step says WHAT to do in the agent's own tools and HOW you'll know it's
  done, with concrete targets. GOOD: "List the project and run `pytest -q` to
  learn the real filenames and see current failures." BAD: "Investigate."
- A LOOK / REVIEW / AUDIT task must END by REPORTING findings — no edits.
- A FIX / CHANGE / IMPROVE task investigates, then makes ONE smallest correct
  change, then VERIFIES it with pytest / py_compile.
- Never invent filenames — refer to areas/patterns; the agent finds real names.
Return ONLY the JSON object, nothing else."""


def _make_plan(task: str, mode: str) -> list[str]:
    """Ask the model for a short ordered work plan. Returns [] on any failure so
    the agent simply runs unplanned (previous behaviour) rather than breaking."""
    try:
        msgs = [{"role": "system", "content": _PLAN_SYSTEM},
                {"role": "user", "content":
                 f"TASK:\n{task[:1500]}\n\nTASK MODE: {mode}\n\n"
                 f"KNOWN LOCATIONS:\n{_known_locations()}\n\n"
                 f"{_playbook_block(task)}\n\nProduce the plan now."}]
        # planning is quick structuring, not deep reasoning — run it fast so every
        # task starts sooner (low effort + small budget = less latency & TPM).
        obj = _llm_json(msgs, must_have="plan", effort="low", max_tokens=700)
        steps: list[str] = []
        for s in (obj or {}).get("plan", []) or []:
            s = str(s).strip().lstrip("0123456789.)-• ").strip()
            if s:
                steps.append(s[:160])
            if len(steps) >= 6:
                break
        return steps
    except Exception:
        log.debug("plan generation failed", exc_info=True)
        return []


def _should_plan(task: str) -> bool:
    """Plan for real, multi-part tasks; skip the overhead for a one-liner lookup."""
    return len(task.split()) >= 5


# ── Real Office document output (Word / Excel / PowerPoint) + truthfulness ─────
# Format keywords, English + Hebrew, checked pptx → xlsx → docx (so "presentation"
# never matches "document").
_FMT_HINTS = (
    ("pptx", ("powerpoint", "power point", ".pptx", " pptx", " ppt", "presentation",
              "slide deck", "slides", "מצגת", "פאוארפוינט", "פאוור פוינט", "שקופיות")),
    ("xlsx", ("excel", "spreadsheet", ".xlsx", " xlsx", "worksheet", "אקסל",
              "גיליון", "גליון")),
    ("docx", ("word file", "word document", "word doc", ".docx", " docx", "doc file",
              "a document file", "קובץ וורד", "קובץ word", "מסמך וורד", "מסמך word",
              "קובץ מסמך", "קובץ .docx")),
)


def _wanted_format(task: str) -> str | None:
    """Which real Office file the user asked for: 'docx' | 'xlsx' | 'pptx' | None."""
    t = " " + task.lower() + " "
    for fmt, words in _FMT_HINTS:
        if any(w in t for w in words):
            return fmt
    return None


def _wants_file(task: str) -> bool:
    return _wanted_format(task) is not None


def _target_wordcount(task: str) -> int:
    """A stated minimum word count: '400 words' / '300 מילים' → int (0 if none)."""
    m = re.search(r"(\d{2,4})\s*(?:words?|מילים|מילה)", task.lower())
    return int(m.group(1)) if m else 0


def _doc_path(task: str, ext: str) -> Path:
    base = re.sub(r"[^\w֐-׿ ]+", "", task)[:40].strip().replace(" ", "_") or "GIL_document"
    docs = Path.home() / "Documents"
    docs.mkdir(parents=True, exist_ok=True)
    return docs / f"{base}.{ext}"


def _verify_saved(out: Path, min_size: int = 200) -> str:
    """Confirm the file is really on disk (non-trivial) — never report a fake save."""
    if not out.exists() or out.stat().st_size < min_size:
        raise RuntimeError(f"{out.suffix} save produced no real file")
    return str(out)


def _save_docx(text: str, task: str) -> str:
    """Markdown-ish prose → a REAL .docx (python-docx)."""
    from docx import Document
    doc = Document()
    for line in (text or "").splitlines():
        s = line.rstrip()
        if not s:
            continue
        if s.startswith("### "):   doc.add_heading(s[4:].strip(), level=3)
        elif s.startswith("## "):  doc.add_heading(s[3:].strip(), level=2)
        elif s.startswith("# "):   doc.add_heading(s[2:].strip(), level=1)
        elif s.lstrip().startswith(("- ", "* ", "• ")):
            doc.add_paragraph(s.lstrip()[2:].strip(), style="List Bullet")
        else:
            doc.add_paragraph(s)
    out = _doc_path(task, "docx")
    doc.save(str(out))
    return _verify_saved(out)


def _save_xlsx(text: str, task: str) -> str:
    """The markdown table(s) in `text` → a REAL .xlsx (openpyxl)."""
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook(); ws = wb.active
    header_done = False
    for line in (text or "").splitlines():
        s = line.strip()
        if not (s.startswith("|") and s.endswith("|")):
            continue
        cells = [c.strip().strip("*`") for c in s.strip("|").split("|")]
        if cells and all(set(c) <= set("-: ") for c in cells if c):   # separator row
            continue
        ws.append(cells)
        if not header_done:
            for c in ws[ws.max_row]:
                c.font = Font(bold=True)
            header_done = True
    if ws.max_row == 0:                    # no table found → one column of lines
        for l in (text or "").splitlines():
            if l.strip():
                ws.append([re.sub(r"^[#\-*•]\s*|\*+", "", l).strip()])
    for col in ws.columns:                 # sensible column widths
        w = max((len(str(c.value)) for c in col if c.value is not None), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(60, w + 2)
    out = _doc_path(task, "xlsx")
    wb.save(str(out))
    return _verify_saved(out)


def _save_pptx(text: str, task: str) -> str:
    """Slide blocks in `text` → a REAL .pptx (python-pptx): each block becomes a
    slide with a title, on-screen bullets, and any Speaker script as slide notes."""
    from pptx import Presentation
    prs = Presentation()
    raw = re.sub(r"^\s*#{0,6}\s*SLIDE[^\n]*$", "---", text or "",
                 flags=re.IGNORECASE | re.MULTILINE)
    blocks = [b.strip() for b in re.split(r"\n-{3,}\n|\n(?=#{1,3}\s)", raw) if b.strip()]
    if not blocks:
        blocks = [text or ""]
    for blk in blocks[:40]:
        lines = [l.rstrip() for l in blk.splitlines() if l.strip()]
        if not lines:
            continue
        title = re.sub(r"^#{1,6}\s*|\*+", "", lines[0]).strip()[:120] or "Slide"
        bullets, notes, in_notes = [], [], False
        for l in lines[1:]:
            low = l.lower()
            if any(k in low for k in ("speaker", "script", "notes", "דובר", "נאום")):
                in_notes = True; continue
            if any(k in low for k in ("visual", "layout", "directive", "עיצוב")):
                continue
            clean = re.sub(r"^[\-\*•>]\s*|\*+|`", "", l).strip()
            if clean:
                (notes if in_notes else bullets).append(clean)
        slide = prs.slides.add_slide(prs.slide_layouts[1])   # Title + Content
        slide.shapes.title.text = title
        tf = slide.placeholders[1].text_frame
        tf.text = bullets[0] if bullets else ""
        for b in bullets[1:]:
            tf.add_paragraph().text = b
        if notes:
            slide.notes_slide.notes_text_frame.text = "\n".join(notes)
    out = _doc_path(task, "pptx")
    prs.save(str(out))
    return _verify_saved(out)


_SAVERS = {"docx": _save_docx, "xlsx": _save_xlsx, "pptx": _save_pptx}
_FMT_LABEL = {"docx": "Word", "xlsx": "Excel", "pptx": "PowerPoint"}


def _save_document(text: str, task: str, fmt: str) -> str:
    """Create the requested Office file, verified on disk; returns its real path."""
    return _SAVERS[fmt](text, task)


_VERIFY_SYSTEM = """You are a strict, independent verification agent. You are shown \
the user's ORIGINAL REQUEST, G.I.L.'s FINAL ANSWER, and the ACTUAL TOOL RESULTS from \
the run. Judge ONE thing: does the answer truthfully and fully do what the user \
asked, with every factual claim grounded in the tool results? Reply with ONE JSON \
object, nothing else:
{"ok": true|false, "reason": "<one short sentence>", "fix": "<if not ok: exactly what GIL must say/do instead>"}
Set ok=FALSE when the answer:
- claims an action succeeded (file created / found / opened, command ran, task done)
  that the tool results do NOT confirm — or that a result shows FAILED ("not found",
  "ERROR", "File not found");
- invents a file path, filename, or fact that is not present in the tool results;
- does not actually answer the question or deliver what was asked.
When a claim is not clearly grounded in the results, choose ok=false. Be concrete."""


def _verify_answer(task: str, answer: str, results_blob: str) -> dict:
    """Independent agent that compares the request, the answer, and the real tool
    results — catching GIL claiming success/files it never confirmed. Never blocks
    on its own error (returns ok:True if the verifier is unreachable)."""
    msgs = [{"role": "system", "content": _VERIFY_SYSTEM},
            {"role": "user", "content":
             f"USER REQUEST:\n{task[:800]}\n\nGIL'S FINAL ANSWER:\n{answer[:2500]}\n\n"
             f"ACTUAL TOOL RESULTS FROM THIS RUN:\n{(results_blob or '(no tools were run)')[:3000]}"}]
    out = _llm_json(msgs, must_have="ok", temperature=0.0)
    if not out:
        return {"ok": True, "reason": "(verifier unavailable)", "fix": ""}
    return {"ok": bool(out.get("ok", True)),
            "reason": str(out.get("reason", ""))[:200],
            "fix": str(out.get("fix", ""))[:400]}


def _plan_status_block(plan: list[str], idx: int) -> str:
    """Render the plan as a live checklist with the current focus marked, so the
    agent always sees where it is and what's left — its compass every turn."""
    if not plan:
        return ""
    rows = []
    for i, step in enumerate(plan):
        mark = "[x]" if i < idx else ("[>]" if i == idx else "[ ]")
        rows.append(f"  {mark} {i + 1}. {step}")
    if idx < len(plan):
        tail = (f"CURRENT FOCUS -> step {idx + 1}: {plan[idx]}\n"
                "Work ONLY this step. The moment it is genuinely satisfied, reply "
                'with {"tool":"plan_next"} to check it off and advance. Never repeat '
                "a step you already did.")
    else:
        tail = ("ALL STEPS ARE CHECKED. Finish NOW with `done` — your summary is the "
                "final report.")
    return ("WORK PLAN (this is YOUR plan; do the steps in order, one at a time):\n"
            + "\n".join(rows) + "\n\n" + tail)


def _action_sig(action: dict) -> str:
    """A stable signature of an action (tool + its main target) used to detect
    thrashing — the same read/search/edit proposed over and over."""
    a = action.get("args") or {}
    tgt = str(a.get("path") or a.get("pattern") or a.get("command")
              or a.get("dir") or "").lower().strip()
    return f"{str(action.get('tool', '')).lower()}:{tgt}"


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


def _opening_ack(task: str) -> str:
    """A guaranteed, CODE-generated opening line — Python decides this, not the
    model, so the very first thing a run shows always names the real project
    being read, instead of depending on whatever the model's own first `why`
    happens to say (which could be anything, or nothing useful)."""
    t = task.lower()
    if any(w in t for w in ("gil project", "gil's project", "gil's code",
                            "your project", "your code", "this project", "yourself")):
        label = "the G.I.L. project"
    else:
        m = re.search(r"[A-Za-z]:\\[^\s\"']+", task)   # a path mentioned in the task
        if m:
            mp = Path(m.group(0))
            folder = mp.parent.name if "." in mp.name else mp.name   # file -> its folder
        else:
            folder = Path(os.getcwd()).name
        label = f"the `{folder}` project"
    return f"Reading {label}'s entry point and layout."


def _assemble_report_from_transcript(messages: list, task: str) -> str:
    """Last-resort report built purely in Python from the tool results already in
    the transcript — files inspected, patterns searched, concrete `file:line`
    hits — so even a hard rate-limit wall yields real findings instead of a blank
    'keep going' apology. This is the floor; the model synthesis above is better."""
    files, searches, hits = [], [], []
    for m in messages:
        c = m.get("content", "")
        if m.get("role") == "assistant":
            try:
                a = json.loads(c)
            except Exception:
                continue
            tool, ar = a.get("tool"), (a.get("args") or {})
            if tool == "read_file" and ar.get("path"):
                files.append(str(ar["path"]))
            elif tool == "search" and ar.get("pattern"):
                searches.append(str(ar["pattern"]))
        elif m.get("role") == "user" and c.startswith("RESULT:"):
            for line in c[7:].splitlines():
                if re.search(r":\d+:", line) and len(line.strip()) > 3:
                    hits.append(line.strip()[:120])
    files = list(dict.fromkeys(files))[:8]
    searches = list(dict.fromkeys(searches))[:6]
    hits = hits[:12]
    out = [f"**Ran out of steps before finishing** — here's what I gathered on "
           f"*{task[:80]}*:", ""]
    if hits:
        out.append(f"**{len(hits)} candidate location(s)** surfaced by the searches I ran:")
        out += [f"- `{h}`" for h in hits]
        out.append("")
    if files:
        out.append("**Files inspected:** "
                   + ", ".join(f"`{Path(f).name}`" for f in files))
    if searches:
        out.append("**Patterns searched:** " + ", ".join(f"`{s}`" for s in searches))
    if not (hits or files or searches):
        out.append("I didn't reach any concrete findings before the limit.")
    out += ["", "Say **continue** and I'll resume from here with a fresh step budget."]
    return "\n".join(out)


# ── Early convergence & step budgeting ────────────────────────────────────────
# A broad goal ("look at the gil project for improvements") used to burn all 20
# steps re-reading the same files, and with reasoning latency plus 8k-TPM backoff
# that ran past 15 minutes without ever producing a report. The agent now stops
# as soon as it has stopped LEARNING, and hard-caps wall-clock time either way.

_READ_TOOLS = ("read_file", "list_dir", "search")
_ACTION_TOOLS = ("edit_file", "write_file", "run")
_DRY_LIMIT = 3


def _time_budget(task_mode: str) -> float:
    """Wall-clock ceiling for one agent run, in seconds (0 disables)."""
    raw = (os.getenv("GIL_AGENT_TIME_BUDGET") or "").strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    # investigation should feel like a query, not a build; changes get longer
    return 300.0 if task_mode == "investigate" else 600.0


class _Convergence:
    """Tracks whether the run is still learning anything.

    An ACTION (edit/write/run) is always progress. A READ counts as progress
    only when it returns something genuinely new — a repeat of a result already
    seen, an error, or a near-empty body is 'dry'. `_DRY_LIMIT` dry reads in a
    row means the agent is circling, so we synthesize the report and stop rather
    than spend the remaining steps and the user's patience.
    """

    def __init__(self, dry_limit: int = _DRY_LIMIT, time_budget: float = 0.0,
                 now=time.time):
        self.dry = 0
        self.dry_limit = dry_limit
        self.seen: set[str] = set()
        self.progress = 0
        self._now = now
        self.deadline = (now() + time_budget) if time_budget else 0.0

    @staticmethod
    def _digest(text: str) -> str:
        import hashlib
        norm = re.sub(r"\s+", " ", text or "").strip().lower()
        return hashlib.md5(norm.encode("utf-8", "replace")).hexdigest()

    def record(self, tool: str, result: str) -> None:
        if tool in _ACTION_TOOLS:
            self.dry = 0                       # real work happened
            self.progress += 1
            return
        if tool not in _READ_TOOLS:
            return                             # plan_next/done are bookkeeping
        text = (result or "").strip()
        barren = (not text or text.upper().startswith("ERROR") or len(text) < 40)
        digest = self._digest(text)
        if barren or digest in self.seen:
            self.dry += 1
        else:
            self.seen.add(digest)
            self.dry = 0
            self.progress += 1

    def out_of_time(self) -> bool:
        return bool(self.deadline and self._now() > self.deadline)

    def stalled(self) -> bool:
        return self.dry >= self.dry_limit

    def should_conclude(self) -> bool:
        return self.stalled() or self.out_of_time()

    def reason(self) -> str:
        if self.stalled():
            return f"{self.dry} reads in a row with nothing new"
        if self.out_of_time():
            return "time budget reached"
        return ""


def _ground_report(summary: str, edits_made: int, task_mode: str) -> str:
    """Deterministic anti-fabrication backstop. In investigate mode GIL changed
    NOTHING, so a 'Plus N quality fixes' section (which describes work done) can
    only be invented — the exact bug where GIL claimed it 'removed comments and
    whitespace' during a read-only run. Cut it."""
    if not summary or edits_made > 0 or task_mode != "investigate":
        return summary
    cut = re.split(r"\n+\**\s*Plus\s+\d*\s*quality\s+fix",
                   summary, maxsplit=1, flags=re.IGNORECASE)
    return cut[0].rstrip() if len(cut) > 1 else summary


def _finalize_report(messages: list, task: str, edits_made: int,
                     task_mode: str = "change") -> tuple[str, bool]:
    """Out of steps / stuck → never a blank fallback. First ask the model to
    compile whatever it found into the real engineering report (one shot, riding
    the same 429/413 backoff). If even that can't be reached, fall back to a
    transcript-assembled report so the user ALWAYS gets findings."""
    ask = list(messages) + [{"role": "user", "content":
        "You are OUT OF STEPS for this task — do NOT request another tool. Reply "
        "with ONE JSON object and nothing else: "
        '{"summary": "<final engineering report in markdown: open with one sharp '
        "line and the number of concrete findings; then a Markdown TABLE of the "
        "findings (columns: # | Finding | Location | Fix) so it renders as a chart; "
        "then a numbered list, most important first, each bolding its first 3-6 "
        "words, giving `file:line`, why it matters and the fix; end with what to "
        "run next>\", "
        '"success": true/false}. Ground every point in what you actually read or '
        "ran above — no guesses, no padding."}]
    try:
        obj = _llm_json(ask, must_have="summary")
    except Exception:
        obj = None
    if obj and str(obj.get("summary", "")).strip():
        return (_ground_report(str(obj["summary"])[:8000], edits_made, task_mode),
                bool(obj.get("success", True)))
    return _ground_report(_assemble_report_from_transcript(messages, task),
                          edits_made, task_mode), False


def run_agent(eng, task: str, hints: list[str] | None = None) -> None:
    """Execute the agent loop (call from a worker thread)."""
    import activity
    import error_fixer

    window = eng.window
    code_mode = bool(getattr(eng, "_code_mode", False))
    task_mode = _task_mode(task)      # 'investigate' (report only) vs 'change'

    # The task message lives in a fixed slot (messages[1]) that _cap_context
    # always preserves. We keep the immutable brief in `base_task_msg` and append
    # a LIVE plan checklist that we re-render each step, so the agent always sees
    # its plan and current focus without the context growing.
    base_task_msg = (
        f"TASK:\n{task[:2500]}\n\n"
        f"KNOWN LOCATIONS (use these — do NOT guess paths):\n"
        f"{_known_locations()}\n\n"
        f"WHERE THINGS LIVE IN G.I.L. (most relevant to this task first — go "
        f"STRAIGHT to the right file, do not grep the tree to find it):\n"
        f"{_project_map(task)}\n\n"
        f"{_playbook_block(task)}\n\n"
        f"Start with the toolkit above on the relevant known location." +
        (f"\n\nSUGGESTED FIX COMMANDS (verify before using):\n" +
         "\n".join(hints) if hints else ""))
    _sys = (_SYSTEM.replace("{steps}", str(_MAX_STEPS))
            .replace("{language}", _deliverable.LANGUAGE_RULE)
            .replace("{deliverable}", _deliverable.MANIFESTO))
    messages = [{"role": "system", "content": _sys},
                {"role": "user", "content": base_task_msg}]

    eng._agent_running = True         # so a new /codemode message won't spawn a rival task
    if hasattr(window, "set_working"):
        window.set_working(True)      # pin the spinner + "working Ns" for the whole run
    if code_mode:
        window.add_chat_message(_opening_ack(task), "gil")   # deterministic hook line
    activity.set_group("Coding agent")

    # ---- Build the work plan up front (the "designed, smart plan" GIL follows) ----
    content_task = _is_content_task(task)
    # tell the model the exact structure to emit for Excel / PowerPoint so GIL can
    # turn the `done` text into a real .xlsx / .pptx (Word/prose needs no hint).
    _fmt_req = _wanted_format(task) if content_task else None
    if _fmt_req == "xlsx":
        messages[1]["content"] += ("\n\nEXCEL OUTPUT: put the data as ONE Markdown "
            "table (header row + rows) as your `done` summary — that table becomes "
            "the real spreadsheet. Rich, complete rows; no prose around it.")
    elif _fmt_req == "pptx":
        messages[1]["content"] += ("\n\nPOWERPOINT OUTPUT: emit one block per slide, "
            "each starting with `## <Slide Title>`, then `- ` on-screen bullets, then "
            "a line `Speaker script:` followed by 3-4 sentences (these become the "
            "slide's speaker notes). 5-8 slides.")
    # a pure deliverable is one rich `done`, not a multi-step tool plan
    plan: list[str] = [] if content_task else (
        _make_plan(task, task_mode) if _should_plan(task) else [])
    plan_idx = 0            # which plan step is the current focus
    item_steps = 0         # tool-steps spent on the current plan step
    from collections import Counter
    sig_counts: Counter = Counter()

    def _refresh_plan_view() -> None:
        block = _plan_status_block(plan, plan_idx)
        messages[1]["content"] = base_task_msg + (("\n\n" + block) if block else "")

    if plan:
        # The plan drives execution internally (injected into messages[1]) but is
        # NOT shown to the user — a brief activity blip only.
        activity.done(activity.start("brain", "Planned the work"), f"{len(plan)} steps")
    _refresh_plan_view()

    # ---- live "thought stream": gpt-oss reasoning tokens typewriter into the
    # terminal as GIL thinks, instead of a silent wait then a sudden dump. Only
    # in the code terminal, and only if the window bridge exposes the hook. ----
    _stream_ok = code_mode and hasattr(window, "stream_thought")
    _tbuf: list[str] = []
    def _on_thought(delta: str) -> None:
        _tbuf.append(delta)
        if sum(len(x) for x in _tbuf) >= 18 or "\n" in delta:
            chunk = "".join(_tbuf); _tbuf.clear()
            try:
                window.stream_thought(chunk)
            except Exception:
                pass
    def _end_thought() -> None:
        if not _stream_ok:
            return
        if _tbuf:
            try:
                window.stream_thought("".join(_tbuf))
            except Exception:
                pass
            _tbuf.clear()
        try:
            window.stream_thought_end()
        except Exception:
            pass
    thought_cb = _on_thought if _stream_ok else None

    edits_made, verified = 0, False
    bad_replies = 0
    recovered_once = False
    rejected_flag = False           # a change got bounced → reason harder next step
    content_retries = 0             # empty/closure-only deliverable → force a rewrite
    verify_retries = 0              # answer failed the truth check → force a redo
    heal_counts: Counter = Counter()  # per-target failure count → self-heal badge (#9)
    speed_mode = _agent_speed_mode()
    if code_mode and speed_mode != "auto":
        activity.instant("brain", f"Speed mode: {speed_mode}",
                         "medium reasoning" if speed_mode == "fast" else "deep reasoning",
                         status="done")
    eng._agent_cancel = False     # cleared at the start of every run (Feature #1)
    # let the long waits inside this thread (streaming call, rate-limit backoff)
    # see a Stop the instant it arrives instead of at the next step boundary
    set_thread_cancel_check(lambda: getattr(eng, "_agent_cancel", False))
    conv = _Convergence(time_budget=_time_budget(task_mode))
    # one slow command must not eat the whole budget before we can converge
    set_thread_run_timeout(60.0 if task_mode == "investigate" else 240.0)
    try:
        for step in range(1, _MAX_STEPS + 1):
            # ── user interrupt (Esc / Stop): settle the state machine to idle and
            # report cleanly, instead of finishing the task. ──
            if getattr(eng, "_agent_cancel", False):
                _end_thought()
                activity.instant("code", "Task stopped", "cancelled by user", status="fail")
                window.add_chat_message(
                    "**Stopped** — cancelled by you." +
                    (f"\n\n({edits_made} change{'s' if edits_made != 1 else ''} already applied)"
                     if edits_made else ""), "gil")
                return
            # ── early convergence: stop when we've stopped learning ──
            # Either the reads have gone dry or the wall-clock budget is spent.
            # Compile what we actually found instead of burning the rest of the
            # steps (and 15 minutes) rediscovering it.
            if conv.should_conclude():
                _end_thought()
                _why_stop = conv.reason()
                activity.instant("code", "Converged early", _why_stop, status="done")
                summary, ok = _finalize_report(messages, task, edits_made, task_mode)
                window.add_chat_message(
                    ("**Solved** ✓ — " if ok else "**Here's what I found** — ") + summary +
                    (f"\n\n({edits_made} file change{'s' if edits_made != 1 else ''} applied)"
                     if edits_made else "") +
                    f"\n\n_Wrapped up after {step - 1} steps — {_why_stop}._", "gil")
                return
            _refresh_plan_view()     # keep the plan checklist current every turn
            # Adaptive reasoning: investigation steps run fast (medium effort,
            # trimmed context → no 429 backoff); once real changes are on the
            # table we reason deep. The context cap follows the profile so a fast
            # step also carries a lighter payload.
            _cur = (plan[plan_idx].lower() if plan and plan_idx < len(plan) else "")
            deep_phase = (edits_made > 0 or rejected_flag or task_mode == "change"
                          and any(w in _cur for w in ("edit", "fix", "change", "write",
                                  "replace", "patch", "implement", "add ")))
            prof = _step_profile(speed_mode, deep_phase)
            if content_task:
                # MEDIUM (not high) effort: high reasoning burns the token budget
                # thinking and sometimes emits an EMPTY document. Medium leaves the
                # bulk of the 5k reply tokens for the actual text. Prompt is small
                # (no file dumps) so this stays under the 8k-TPM ceiling.
                prof = {"effort": "medium", "max_tokens": 5000, "ctx": 8000}
            _cap_context(messages, prof["ctx"])   # profile-sized payload under the TPM cap
            # Live token-budget gauge: estimate the working-context size (~4 chars
            # per token) and push it to the diagnostic bar so the user can see how
            # full GIL's context is and when `/compact` would help.
            if hasattr(window, "token_usage"):
                try:
                    _tok = sum(len(str(m.get("content", ""))) for m in messages) // 4
                    window.token_usage({"tokens": _tok, "budget": _CTX_BUDGET,
                                        "step": step, "max": _MAX_STEPS})
                except Exception:
                    pass
            # Second-to-last step: tell GIL to land the plane himself now — a clean
            # self-authored `done` beats the forced finalize we'd otherwise do.
            if step == _MAX_STEPS - 1:
                messages.append({"role": "user", "content":
                    "You are on your SECOND-TO-LAST step. Do not start new work — "
                    "finish NOW with `done` and your full findings report."})
            action = _llm(messages, on_thought=thought_cb,
                          effort=prof["effort"], max_tokens=prof["max_tokens"])
            _end_thought()           # settle the live thought line for this step
            # A Stop that landed during the call must not still run this tool.
            if getattr(eng, "_agent_cancel", False):
                continue             # loop top reports the clean stop and returns
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
                if not recovered_once:
                    # A dense recent tool result (a huge file dump) is the most
                    # common reason a small model starts replying with garbage.
                    # Trim the largest recent RESULT and give it ONE clean retry
                    # before actually giving up — this is why tasks were "cutting
                    # off" right after a big read_file/search step.
                    recovered_once, bad_replies = True, 0
                    _trim_biggest_result(messages)
                    messages.append({"role": "user", "content":
                        "Take a smaller, more targeted next step."})
                    continue
                # Don't leave a blank "keep going" line — compile whatever GIL
                # gathered so far into the real report before stopping.
                _end_thought()
                summary, ok = _finalize_report(messages, task, edits_made, task_mode)
                activity.instant("code", "Compiled findings", _speakable(summary)[:90],
                                 status="done" if ok else "fail")
                window.add_chat_message(
                    ("**Solved** ✓ — " if ok else "**Stopped** — ") + summary +
                    (f"\n\n({edits_made} file change{'s' if edits_made != 1 else ''} applied)"
                     if edits_made else ""), "gil")
                return
            bad_replies = 0
            tool = str(action.get("tool", ""))
            args = action.get("args") or {}
            why  = str(action.get("why", tool))[:90]

            if tool == "done":
                # Room for a real structured report (table + findings + proof).
                # Tolerate the model putting summary/success at the top level
                # instead of under `args` — the `done` signature invites both.
                summary = str(args.get("summary") or action.get("summary") or "").strip()
                ok = bool(args.get("success", action.get("success", True)))
                # anti-fabrication: an investigation that changed nothing cannot
                # have "quality fixes" — strip a claimed-changes section (#trust)
                summary = _ground_report(summary, edits_made, task_mode)
                # DELIVERABLE GUARD: a content task must ship the FULL text, not an
                # empty / "file is ready, attached" closure. If the model short-
                # circuits, reject it and force a real write (up to 2 retries).
                if content_task and content_retries < 2:
                    _words = len(summary.split())
                    _target = _target_wordcount(task)
                    _too_short = len(summary) < 250
                    # only ONE expand-retry for a mild length shortfall (keeps latency
                    # bounded under the TPM wall); always retry a truly-empty result.
                    _undershoot = (_target and _words < int(_target * 0.7)
                                   and content_retries < 1)
                    if _too_short or _undershoot:
                        content_retries += 1
                        _why = ("empty / closure-only" if _too_short
                                else f"only {_words} words vs the requested {_target}")
                        activity.instant("code", "Deliverable too short — expanding",
                                         _why[:50], status="fail")
                        messages.append({"role": "assistant", "content": json.dumps(action)})
                        messages.append({"role": "user", "content":
                            "RESULT:\nYour deliverable is too short (" + _why + "). You "
                            "CANNOT attach a file — the TEXT is the deliverable. Write the "
                            "COMPLETE document NOW as the `done` summary: title, `##` "
                            "headings, full paragraphs, in the user's language, and "
                            + (f"AT LEAST {_target} words — over-shoot to about "
                               f"{int(_target * 1.2)}. " if _target
                               else "meeting the requested length. ")
                            + "Never say 'attached' / 'file ready' / 'הקובץ מוכן' / 'מצורף'."})
                        continue
                summary = (summary or "Task finished.")[:8000]

                # ── TRUTH CHECK (tool tasks): an independent agent compares the
                # request ↔ answer ↔ actual tool results, so GIL cannot claim a
                # file/action succeeded that the results never confirmed. One redo. ──
                if not content_task and ok and verify_retries < 1:
                    results_blob = "\n".join(
                        str(m.get("content", ""))[:600] for m in messages[-18:]
                        if m.get("role") == "user"
                        and str(m.get("content", "")).startswith("RESULT:"))
                    verdict = _verify_answer(task, summary, results_blob)
                    if not verdict["ok"]:
                        verify_retries += 1
                        activity.instant("brain", "Answer failed the truth check",
                                         verdict["reason"][:70], status="fail")
                        messages.append({"role": "assistant", "content": json.dumps(action)})
                        messages.append({"role": "user", "content":
                            "RESULT:\nSELF-CHECK FAILED — your answer does not match what "
                            "actually happened: " + verdict["reason"] + " "
                            + (verdict["fix"] or "State the TRUTH grounded in the RESULTs "
                               "above. Never claim a file/action succeeded unless a RESULT "
                               "confirms it, and never invent a path or filename.")
                            + " Redo `done` truthfully now — if it FAILED, say so plainly."})
                        continue

                # ── real Office file (Word / Excel / PowerPoint) — actually create
                # it, verify it on disk, and report the REAL path (never a fake save). ──
                file_note = ""
                _fmt = _wanted_format(task) if (content_task and ok) else None
                if _fmt:
                    try:
                        saved = _save_document(summary, task, _fmt)
                        file_note = f"\n\n📄 Saved a real {_FMT_LABEL[_fmt]} file → `{saved}`"
                    except Exception as exc:
                        file_note = ("\n\n⚠️ I wrote the content above but could not save "
                                     f"the {_FMT_LABEL[_fmt]} file ({exc}).")

                activity.instant("code", "Agent finished",
                                 _speakable(summary)[:100], status="done" if ok else "fail")
                if content_task and ok:
                    # a deliverable: the document renders FIRST; the closure is a
                    # small footer at the bottom, never a header before the content.
                    msg = summary + file_note + "\n\n— ✓ ready"
                else:
                    msg = (("**Solved** ✓ — " if ok else "**Stopped** — ") + summary +
                           (f"\n\n({edits_made} file change{'s' if edits_made != 1 else ''} applied)"
                            if edits_made else ""))
                window.add_chat_message(msg, "gil")
                spoken = _speakable(summary)
                if not code_mode:            # code mode is silent (typed surface)
                    window.set_state("speaking", said=spoken)
                    eng._speak(spoken)
                eng._last_spoke_at[0] = time.time() - 1.5
                eng._last_said[0] = spoken
                return

            # ---- plan_next: the agent checks off the current step and advances ----
            if tool == "plan_next":
                if plan and plan_idx < len(plan):
                    activity.instant("code", f"Step {plan_idx + 1} done ✓",
                                     plan[plan_idx][:80], status="done")
                    plan_idx += 1
                    item_steps = 0
                    sig_counts.clear()
                _refresh_plan_view()
                messages.append({"role": "assistant", "content": json.dumps(action)})
                if not plan or plan_idx >= len(plan):
                    messages.append({"role": "user", "content":
                        "RESULT:\nEvery plan step is checked off. Finish NOW with `done` "
                        "and your full report — do not start new work."})
                else:
                    messages.append({"role": "user", "content":
                        f"RESULT:\nChecked off. CURRENT FOCUS is now step {plan_idx + 1}: "
                        f"{plan[plan_idx]}"})
                continue

            # ---- progress / anti-thrash guard ----------------------------------
            # A planned run must keep moving. If the agent repeats the same move,
            # or lingers too long on one step, force it forward instead of letting
            # it spin (the exact loop that left a task unfinished after minutes).
            if plan:
                item_steps += 1
                sig = _action_sig(action)
                sig_counts[sig] += 1
                repeating = sig_counts[sig] >= 3 and tool not in ("done", "plan_next")
                stalled = item_steps > 7 and plan_idx < len(plan) - 1
                if repeating or stalled:
                    moved = ""
                    if plan_idx < len(plan) - 1:
                        plan_idx += 1
                        item_steps = 0
                        sig_counts.clear()
                        moved = f" Advancing to step {plan_idx + 1}: {plan[plan_idx]}."
                    activity.instant("code", "Breaking a loop",
                                     (f"repeated {sig[:52]}" if repeating
                                      else f"step budget reached"), status="fail")
                    _refresh_plan_view()
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content":
                        "RESULT:\n" + (
                            f"You have tried `{sig}` repeatedly without progress. "
                            if repeating else "You have spent enough on this step. ")
                        + "Do NOT repeat it." + moved +
                        (" If the plan is effectively done, finish with `done` and your "
                         "findings." if plan_idx >= len(plan) - 1 else "")})
                    continue

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
                    rejected_flag = True   # reason harder on the retry (auto mode)
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
                    rejected_flag = True   # reason harder on the retry (auto mode)
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
                policy = _approval_policy()          # Feature #5: permission policy
                if policy == "readonly":
                    # investigate-only — refuse the destructive op, keep reading.
                    activity.instant("code", "Blocked · read-only mode",
                                     (why or tool)[:60], status="fail")
                    messages.append({"role": "assistant", "content": json.dumps(action)})
                    messages.append({"role": "user", "content":
                        "RESULT:\nREAD-ONLY MODE is on — file writes, edits and shell "
                        "commands are disabled. Investigate with read/list/search only, "
                        "then finish with `done` reporting exactly what you WOULD change."})
                    continue
                if policy == "auto":
                    # power mode — auto-approve (the destructive-command blocklist and
                    # the automated reviewer/precheck have already vetted this).
                    activity.instant("code", "Auto-approved", (why or tool)[:60], status="done")
                else:
                    # Feature #3: hunk-by-hunk diff review for file edits (web UI).
                    # The user accepts / rejects / edits each hunk; we then apply
                    # exactly the accepted subset as a whole-file write.
                    reviewed = False
                    if (tool in ("edit_file", "write_file")
                            and hasattr(eng.window, "request_hunk_review")):
                        _orig, _prop, _p = _edit_original_proposed(tool, args)
                        if _p is not None and _orig != _prop:
                            _hunks = diff_hunks.build_hunks(_orig, _prop)
                            if _hunks:
                                reviewed = True
                                aid = activity.start(
                                    "code", f"Reviewing {len(_hunks)} hunk(s): {why}")
                                _dec = _ask_hunk_review(eng, str(_p), _hunks, why)
                                if _dec is None:
                                    activity.fail(aid, "review cancelled")
                                    rejected_flag = True
                                    messages.append({"role": "assistant", "content": json.dumps(action)})
                                    messages.append({"role": "user", "content":
                                        "RESULT:\nUser cancelled the diff review without "
                                        "applying it. Try a different approach or finish with done."})
                                    continue
                                _accepted = [
                                    h for h in _hunks
                                    if (_dec.get(h["id"]) or _dec.get(str(h["id"])) or {}
                                        ).get("action") != "reject"]
                                if not _accepted:
                                    activity.fail(aid, "all hunks rejected")
                                    rejected_flag = True
                                    messages.append({"role": "assistant", "content": json.dumps(action)})
                                    messages.append({"role": "user", "content":
                                        "RESULT:\nUser rejected every hunk of this change. Do "
                                        "not repeat it — try a different approach or finish with done."})
                                    continue
                                _newc = diff_hunks.apply_hunks(_orig, _prop, _dec)
                                tool = "write_file"          # apply the accepted subset
                                args = {"path": str(_p), "content": _newc}
                                activity.done(
                                    aid, f"applied {len(_accepted)}/{len(_hunks)} hunk(s)")
                    if not reviewed:
                        aid = activity.start("code", f"Waiting for approval: {why}")
                        approved = _ask_approval(
                            eng, f"*{why}*" + (f"\n\n{desc}" if desc else ""),
                            diff=appr_diff, file=appr_file)
                        if not approved:
                            activity.fail(aid, "declined by user")
                            rejected_flag = True   # reason harder on the retry
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

            # Self-healing visibility (#9): a failed run/edit means GIL will read the
            # error and auto-correct next step — surface that as a badge with the
            # attempt count (the thrash guard caps repeats at 3, then it pivots).
            if not ok and tool in ("run", "edit_file", "write_file"):
                _sig = _action_sig(action)
                heal_counts[_sig] += 1
                _reason = result[6:].strip() if result.startswith("ERROR") else result
                _reason = " ".join(_reason.split())[:110] or "an error"
                if hasattr(window, "self_heal"):
                    try:
                        window.self_heal({"attempt": heal_counts[_sig], "max": 3,
                                          "reason": _reason})
                    except Exception:
                        pass

            conv.record(tool, result)   # did this step actually teach us anything?
            messages.append({"role": "assistant", "content": json.dumps(action)})
            messages.append({"role": "user", "content": f"RESULT:\n{result[:2200]}"})
            # keep context bounded: system + task + last 16 exchanges
            if len(messages) > 34:
                messages = messages[:2] + messages[-32:]

        # Hit the step ceiling without a `done` — gracefully collapse the context
        # and compile the findings so far into the report, instead of a bare
        # "keep going" line the user can't act on.
        _end_thought()
        summary, ok = _finalize_report(messages, task, edits_made, task_mode)
        activity.instant("code", "Compiled findings at step limit",
                         _speakable(summary)[:90], status="done" if ok else "fail")
        window.add_chat_message(
            ("**Solved** ✓ — " if ok else "**Stopped at the step limit** — ") + summary +
            (f"\n\n({edits_made} file change{'s' if edits_made != 1 else ''} applied)"
             if edits_made else ""), "gil")
    finally:
        import activity as _a
        _a.clear_group()
        set_thread_cancel_check(None)   # never leak the probe into the next run
        set_thread_run_timeout(None)    # nor the tool time cap
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
