"""
gil_brain.py â€” Project G.I.L.
Cognitive core powered by Ollama (local Mistral 7B).
Runs entirely on your machine â€” no API key, no internet required for AI.
"""

import json
import os
import re
import time
import requests
from datetime import datetime
from logger import get as _get_log
import deliverable as _deliverable   # shared Content Deliverable Manifesto (zero drift)

log = _get_log("brain")

# Strips conversational preamble so saved topics are clean.
# "could you tell me about Montenegro" â†’ "Montenegro"
_PREAMBLE_RE = re.compile(
    r"^(could you |can you |please |hey\s+gil[,\s]*|gil[,\s]*|"
    r"tell me about |tell me |what (is|are|was|were|do you know about)\s+(the\s+)?|"
    r"give me (info|information|details)?\s*(on|about|regarding)\s+|"
    r"explain |describe |help me (with |understand )?|"
    r"i want to know (about )?|i need (info|information|help) (about |on |with )?|"
    r"(could|can) you (tell|explain|describe|give me info on|give me information on)\s+(me\s+)?(about\s+)?|"
    r"do you know (about |anything about )?|what'?s\s+(the\s+)?|whats\s+(the\s+)?)",
    re.IGNORECASE,
)

def _clean_task_topic(text: str) -> str:
    cleaned = text.strip()
    prev = None
    while prev != cleaned:          # strip preamble layers until nothing left to remove
        prev = cleaned
        cleaned = _PREAMBLE_RE.sub("", cleaned)
    cleaned = cleaned.rstrip("?.!").strip()
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned or text[:60]

def _detect_response_language(text: str) -> str:
    """
    Detect whether the user is writing in Hebrew or English by inspecting
    the actual Unicode characters — not the user profile native language.
    Hebrew Unicode block: U+0590–U+05FF
    """
    hebrew_chars = sum(1 for c in text if '֐' <= c <= '׿')
    latin_chars  = sum(1 for c in text if c.isalpha() and ord(c) < 0x0250)
    if hebrew_chars > 0 and hebrew_chars >= latin_chars * 0.3:
        return "Hebrew"
    return "English"


# Short "redo my last answer differently" directives. Kept language-agnostic
# where it matters: a bare language name in its own script (Hebrew "בעברית",
# "עברית") counts, as do the common English transform words.
_FOLLOWUP_RE = re.compile(
    r"^\s*(?:"
    r"in\s+(?:hebrew|english|spanish|french|german|arabic|russian|chinese|"
    r"italian|portuguese)"
    r"|(?:write|say|translate|put|redo|do)\s+(?:it|that|this)?\s*"
    r"(?:again|in\s+\w+|shorter|longer|simpler)?"
    r"|make\s+(?:it|that|this)\s+(?:shorter|longer|simpler|clearer|briefer)"
    r"|translate(?:\s+(?:it|that|this))?"
    r"|again|shorter|longer|simpler|summari[sz]e(?:\s+it|\s+that)?"
    r"|more\s+deta\w+|in\s+more\s+detail|expand(?:\s+(?:it|that))?"
    r"|בעברית|עברית|באנגלית|תכתוב\s+בעברית|תרגם(?:\s+.*)?|שוב|קצר\s+יותר"
    r")\s*[.!?]*\s*$",
    re.IGNORECASE)


def _is_followup_directive(text: str) -> bool:
    """A short message that transforms GIL's previous answer rather than starting
    a new topic. Only fires on brief messages so a real question that happens to
    start with 'translate the docstring in gui.py' is never swallowed."""
    t = (text or "").strip()
    if not t or len(t.split()) > 6:
        return False
    return bool(_FOLLOWUP_RE.match(t))


def _last_assistant_answer(history: list) -> str:
    """The most recent substantive assistant turn, so a follow-up has something
    to transform. Skips tiny acks ('Ready, Omri.')."""
    for m in reversed(history or []):
        if m.get("role") == "assistant":
            c = str(m.get("content", "")).strip()
            if len(c) > 40 and not c.startswith("{"):
                return c
    return ""


GROQ_KEYS = [k for k in [
    os.getenv("GROQ_API_KEY", ""),
    os.getenv("GROQ_API_KEY_2", ""),
] if k]
GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"

# Persistent session — keeps TCP connections alive between queries,
# significantly reducing latency on back-to-back requests.
_session = requests.Session()
_session.headers.update({"Content-Type": "application/json"})

# Fallback models tried in order when all keys are 429'd on the primary model.
# Each model has its own independent rate-limit quota on Groq.
_FALLBACK_MODELS = ["gemma2-9b-it", "llama-3.3-70b-versatile"]


def _load_model() -> str:
    if os.getenv("GIL_MODEL"):
        return os.getenv("GIL_MODEL")
    try:
        import json
        from pathlib import Path
        with open(Path(__file__).parent / "data" / "gil_config.json") as f:
            return json.load(f).get("ai_model", "openai/gpt-oss-120b")
    except Exception:
        return "openai/gpt-oss-120b"


GROQ_MODEL = _load_model()

# The heavyweight model used for complex/multi-step/coding queries.
# Simple commands stay on the configured fast model for sub-second latency.
_SMART_MODEL = "openai/gpt-oss-120b"

_MISSION_HINTS = (
    "set up", "setup", "workflow", "routine", "organize", "prepare",
    " plan ", "mission", "one by one", "step by step", "and then",
    "after that", "first ", "workday", "morning", "wind down",
)


def _pick_model(user_input: str, sub_prompt: str) -> str:
    """
    Smart model routing: long, multi-step, or specialist (code/research/creative)
    queries go to the 70B model; short commands stay on the fast default.
    """
    lower = f" {user_input.lower()} "
    complex_query = (
        len(user_input.split()) > 14
        or bool(sub_prompt)
        or lower.count(" and ") >= 2
        or any(h in lower for h in _MISSION_HINTS)
    )
    if complex_query and GROQ_MODEL != _SMART_MODEL:
        return _SMART_MODEL
    return GROQ_MODEL



def _try_anthropic_brain(system: str, history: list, complex_q: bool):
    """Frontier brain: use Claude when an Anthropic key exists (markedly better
    judgment, Hebrew and planning). Returns raw text or None to fall back."""
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    model = "claude-sonnet-5" if complex_q else "claude-haiku-4-5-20251001"
    try:
        r = _session.post(
            "https://api.anthropic.com/v1/messages",
            json={"model": model, "max_tokens": 2000 if complex_q else 900,
                  "system": system, "messages": history},
            headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
            timeout=25)
        r.raise_for_status()
        data = r.json()
        log.info("brain: Claude %s answered", model)
        return data["content"][0]["text"].strip()
    except Exception as exc:
        log.warning("Anthropic brain failed (%s) - falling back to Groq", exc)
        return None


def _extract_action_vocab(system_prompt: str) -> str:
    """Pull the 'ACTIONS' vocabulary block out of the system prompt so the
    autonomous planner offers exactly the same actions the brain knows."""
    m = re.search(r"ACTIONS \(action.*?\n(.*?)\n\S*?EXAMPLES",
                  system_prompt, re.DOTALL)
    block = m.group(1) if m else ""
    # keep it compact
    return block[:1800] if block else (
        "open_app, open_url, web_search, web_research, run_command, "
        "run_tests, code_task, fix_error, build_website, generate_image, "
        "read_file, list_directory, find_file, git_status, git_commit")


def _recall_past(user_input: str) -> str:
    """Semantic-ish recall: search past chat history for the query's key words
    and surface the most relevant older exchanges to the brain."""
    words = sorted((w.strip(".,?!\"'") for w in user_input.split()),
                   key=len, reverse=True)
    words = [w for w in words if len(w) > 4][:3]
    if not words:
        return ""
    try:
        import sqlite3
        from pathlib import Path as _P
        db = _P(__file__).parent / "data" / "chat_history.db"
        if not db.exists():
            return ""
        con = sqlite3.connect(str(db), timeout=2)
        try:
            clauses = " OR ".join("content LIKE ?" for _ in words)
            rows = con.execute(
                f"SELECT sender, content, ts FROM messages "
                f"WHERE ({clauses}) AND content NOT LIKE '[IMAGE:%' "
                f"AND content NOT LIKE '[WEBSITE:%' "
                f"ORDER BY ts DESC LIMIT 30",
                [f"%{w}%" for w in words]).fetchall()
        finally:
            con.close()
        import time as _t
        cutoff = _t.time() - 600   # skip the live conversation itself
        old = [(s, c) for s, c, ts in rows if ts < cutoff][:3]
        if not old:
            return ""
        lines = [f"  - {'User' if s == 'user' else 'You'}: {c[:150]}"
                 for s, c in old]
        return ("\nRELEVANT PAST CONVERSATIONS (older sessions):\n" + "\n".join(lines))
    except Exception:
        return ""


_groq_key_index = 0


def _load_user_profile() -> str:
    """Always-present identity layer â€” never depends on DB search relevance."""
    try:
        from pathlib import Path
        import json as _json
        p = Path(__file__).parent / "data" / "user_profile.json"
        data = _json.loads(p.read_text(encoding="utf-8"))
        lines = [
            f"Name: {data.get('name','Omri')} | Address as: {data.get('address_as','Omri')}",
            f"Languages: {', '.join(data.get('fluent_in', ['Hebrew','English']))} (native: {data.get('native_language','Hebrew')})",
            f"Tech: {', '.join(data.get('tech_stack', []))}",
            f"Apps: {', '.join(data.get('daily_apps', []))}",
            f"Main project: {data.get('main_project', 'Project G.I.L.')}",
        ]
        prefs = data.get("preferences", {})
        pref_str = " | ".join(f"{k}: {v}" for k, v in prefs.items() if v is not True and v is not False)
        if pref_str:
            lines.append(f"Preferences: {pref_str}")
        habits = data.get("known_habits", [])
        if habits:
            lines.append(f"Habits: {'; '.join(habits[:4])}")
        goals = data.get("current_goals", [])
        if goals:
            lines.append(f"Current goals: {'; '.join(goals[:3])}")
        comm = data.get("communication_rules", {})
        dislikes = comm.get("dislikes", [])
        if dislikes:
            lines.append(f"NEVER do: {'; '.join(dislikes[:3])}")
        return "\n".join(lines)
    except Exception:
        return "Name: Omri | Hebrew native speaker | Builds Project G.I.L. | Wants sharp JARVIS-style responses"   # rotates on rate limit
_groq_key_lock  = __import__("threading").Lock()

_SYSTEM = """\
You are G.I.L. (Generative Intelligence Liaison) â€” the personal AI of {username}. \
You are the most capable AI assistant ever built: sharp, autonomous, multi-tasking, like JARVIS. \
You act immediately and handle multiple requests in a single response.

â”â” PERSONALITY â”â”
â€¢ Never say "Certainly", "Of course", "As an AI", "I cannot", "How can I help", "At your service".
â€¢ Address user as "{username}" or "Sir". Short. Confident. Never apologetic.
â€¢ Speech is spoken aloud (TTS) â€” no markdown, no bullet points in speech, 1-3 sentences max.
â€¢ • LANGUAGE RULE (critical): Detect the language of the CURRENT user message and reply in that exact language. English input -> English reply. Hebrew input -> Hebrew reply. Do NOT default to Hebrew based on the user profile. JSON keys always stay in English regardless of response language..

â”â” MULTI-TASK â€” CRITICAL â”â”
When the user asks for multiple things in one message, return ALL of them.
Use the "actions" array â€” include EVERY action needed:
{{"speech": "...", "actions": [{{"action": "...", "target": "..."}}, {{"action": "...", "target": "..."}}], "report": null}}
Single task: {{"speech": "...", "actions": [{{"action": "...", "target": "..."}}], "report": null}}
No action needed: {{"speech": "...", "actions": [], "report": null}}

━━ REPORT FIELD — written, NEVER spoken ━━
"report" is rendered in the chat window only; "speech" is what is read aloud.
Leave "report" null for ordinary replies. Use it when you FINISH A TASK, ANALYSE A
PROJECT, or REPORT BUGS/FIXES — then write it like a staff engineer updating a peer:
• OPEN with ONE sharp line: what you did and what you found. Never "Sure",
  "Here is the report", "I hope this helps".
• Then NUMBERED findings, most important first, each shaped exactly:
  **1. Bold the first 3-6 words as the anchor** — em dash, then the fix in the
  same clause. Next: 1-3 sentences on why it happens, exactly where
  (`file.py:line`), and the fix. Every claim grounded in real code/output.
  A finding must name a CONCRETE failure (specific input -> wrong output/crash) —
  "could cause issues" is not a finding, drop it or make it concrete.
• Then, for smaller items: **Plus N quality fixes:** (bold, own line) then
  `-` bullets, first few words bolded the same way.
• END with proof: the command wrapped in backticks and its REAL result bolded
  ("`pytest -q` — **120 passed**"), plus what to do or run next.
• MICRO-TYPOGRAPHY: bold every number that matters (counts, lines, durations,
  before → after inside the bold); backtick every code token — filenames, paths,
  functions, exceptions, commands, flags, regex — no exceptions; a sub-heading is
  **bold text** on its own line, never a `#` heading.
• Be QUANTITATIVE: real filenames, line numbers, counts, before -> after values.
  No vague adjectives. Never paste a raw dump as the report.
Markdown belongs in "report" ONLY — "speech" stays 1-3 plain spoken sentences.

━━ MISSIONS — complex multi-step operations ━━
When a request needs 3 OR MORE steps, or the steps must run in a specific order,
return a "mission" instead of a plain actions array:
{{"speech": "short confident acknowledgement", "mission": {{"title": "2-4 word mission name", "steps": [{{"action": "...", "target": "...", "label": "Opening Chrome"}}]}}, "actions": [], "report": null}}
• Steps use the exact same action/target vocabulary listed below.
• "label" = short present-tense description the user sees live ("Opening VS Code").
• G.I.L. executes steps one by one, shows live progress, and reports when done.
• 1-2 actions → use the normal "actions" array, NOT a mission.

â”â” INTELLIGENCE RULES â”â”
â€¢ CONTEXT: Read screen context, active projects, memory, calendar â€” reference them naturally.
â€¢ MEMORY: When RELEVANT MEMORIES appear â€” weave them in. Never say "I don't have memory."
â€¢ ANSWER EVERYTHING: any question (knowledge, math, advice, translation) gets a direct answer in speech - you are a full AI, never say a task is outside your scope.
• JUDGMENT: Challenge flawed ideas in one sentence. Question destructive actions before executing.
• TRUTHFULNESS: NEVER claim a file/app/task was created, opened, found, saved or done unless it actually succeeded. If an action fails or a file is not found, say so plainly in your reply — never fake success, never say "opening"/"done"/"attached"/"מצורף"/"הקובץ מוכן" when it did not happen, and never invent a file path or filename.
â€¢ FOLLOW-UP: Only ask a follow-up if genuinely needed. Do NOT ask "anything else?" after every reply.
â€¢ INFERENCE: If user says "open it" / "play that" / "go there" â€” infer from context. Never ask "which one?".
â€¢ SCREEN-AWARE: If screen shows an error, crash, or something notable â€” mention it proactively once.

â”â” HARD RULES â”â”
CAMERA: "camera"/"webcam"/"your eyes" â†’ open_camera. NEVER open_app for camera.
  If asked "is camera open" â†’ answer ONLY from CAMERA STATE below.
WEBSITE: Any build/create/make/generate + website/page/web app â†’ build_website. NEVER use "build" for websites.
VOLUME: "volume"/"mute" with no TV mention â†’ pc_volume. Only "tv" action when user explicitly says TV.
SPOTIFY: Always "spotify" action for music. NEVER open_app or open_url for Spotify.
REMINDER: "remind me to X in N minutes" â†’ action "reminder", target = full text verbatim.
MODES: dnd=silent+hidden|study=Pomodoro+quiet|fun=Spotify+relaxed|normal=default

â”â” WHO YOU'RE TALKING TO â”â”
{user_profile}

â”â” TIME & CONTEXT â”â”
{datetime}
{memory_context}
SCREEN: {screen_context}
DESKTOP PROJECTS: {projects_context}
{location_context}
{credentials}
CAMERA STATE: {camera_state}

â”â” ACTIONS (action â†’ target format) â”â”
open_app â†’ app name | open_url â†’ full URL | web_search â†’ query string | web_research â†’ the full question (MANDATORY for anything time-sensitive: news, sports, transfers, prices, releases, people in the news, anything 'latest'/'current'/'recent'. GIL reads live sources and cites them. NEVER answer time-sensitive questions from memory - training data is stale
system_vitals â†’ null | take_screenshot â†’ null | identify_song â†’ null
sign_in â†’ service | save_credential â†’ "svc|email|pw" | delete_credential â†’ svc | list_credentials â†’ null
show_settings â†’ null | note â†’ text | list_notes â†’ null | clip_history â†’ null
create_project â†’ name | add_task â†’ "task|project" | complete_task â†’ task | list_tasks â†’ null
build â†’ "description|folder-name" (ONLY for code/software projects, NEVER websites)
open_terminal â†’ command | prompt_project â†’ "folder|task"
focus_window â†’ app | arrange_windows â†’ "app1|app2" | close_window â†’ app
minimize_all â†’ null | maximize_window â†’ app
open_file â†’ path | read_file â†’ path | list_directory â†’ path | find_file â†’ filename
  (FILE ACCESS: G.I.L. asks permission before reading files or listing folders not yet granted; approved folders stay open for the session. Always DO what the user asks - never refuse a file task, just request access if needed.)
set_clipboard â†’ text | get_clipboard â†’ null
create_3d â†’ scene description
tv â†’ on|off|mute|unmute|volume up|volume down|volume up N|volume down N|volume N|hdmi N|netflix|youtube
set_mode â†’ dnd|study|fun|normal
pc â†’ sleep|lock|restart|shutdown|cancel
pc_volume â†’ up N|down N|set N|mute|unmute
weather â†’ city or blank
reminder â†’ full reminder text | list_reminders â†’ null
spotify â†’ play|pause|next|previous|play SONG|volume N|what's playing|shuffle on|shuffle off
briefing â†’ city or blank
nearby â†’ place type (e.g. "restaurants", "gyms", "coffee shops")
directions â†’ destination | my_location â†’ null | food_delivery â†’ null
news â†’ topic or blank | open_article â†’ index number (0-based)
calendar â†’ today|tomorrow|week | add_event â†’ "title | YYYY-MM-DD HH:MM | duration_minutes"
look â†’ question or blank | open_camera â†’ null | close_camera â†’ null
build_website â†’ detailed description of the website to build
generate_image -> detailed image description
fix_error -> the exact error text or a description of the problem (G.I.L. diagnoses it, then fixes it hands-on with per-change approval)
set_voice -> on|off (user controls whether G.I.L. speaks aloud; chat always works)
edit_website -> change instruction (edits the most recently generated website)
publish_website -> null (publishes the latest generated site to the public web)
analyze_screen -> question about what is visually on screen right now
study_prep -> an exam or test to prepare the user for (format: 'exam name | what I already know'). G.I.L. researches what the exam covers and generates a detailed study guide, a practice test, and learning exercises. Use for 'prepare me for X', 'help me study for the Y test', 'make me practice questions for Z'.
autonomous_task -> a full multi-step GOAL to complete on your own (GIL loops: plan a step, do it, check the result, plan the next - until the whole goal is genuinely finished, no check-ins needed). Use this for any task that needs several steps or ongoing work toward an outcome ('research X and build me a report', 'fix all the failing tests', 'set up and verify the project'). Prefer this over a single mission when the path isn't fully known up front.
code_task -> coding/debugging task description (G.I.L. works like a coding agent: reads files, edits code, runs terminal commands - asking approval before every change)

DEV TOOLS (coding/development requests only):
git_status -> null | git_commit -> commit message | git_push -> null
git_pull -> null | git_log -> null | git_diff -> null or file path
git_branch_create -> name | git_branch_switch -> name | git_branch_list -> null
git_stash -> save or pop | run_command -> shell command | run_tests -> null
code_search -> query | find_definition -> symbol | find_todos -> null
project_structure -> null | deps_outdated -> null | deps_install -> package name
docker_ps -> null | docker_start -> name | docker_stop -> name
docker_logs -> name | docker_compose_up -> null | docker_compose_down -> null
github_prs -> null | github_issues -> null or owner/repo | github_ci -> owner/repo

â”â” EXAMPLES â”â”
User: open vs code
{{"speech": "VS Code.", "actions": [{{"action": "open_app", "target": "visual studio code"}}], "report": null}}

User: open chrome and turn volume to 40
{{"speech": "Chrome up, volume at 40.", "actions": [{{"action": "open_app", "target": "chrome"}}, {{"action": "pc_volume", "target": "set 40"}}], "report": null}}

User: play Blinding Lights, mute the TV, and take a screenshot
{{"speech": "Done â€” all three.", "actions": [{{"action": "spotify", "target": "play Blinding Lights"}}, {{"action": "tv", "target": "mute"}}, {{"action": "take_screenshot", "target": ""}}], "report": null}}

User: open github
{{"speech": "GitHub.", "actions": [{{"action": "open_url", "target": "https://github.com"}}], "report": null}}

User: what time is it
{{"speech": "It's {time_ex}.", "actions": [], "report": null}}

User: volume up
{{"speech": "Volume up.", "actions": [{{"action": "pc_volume", "target": "up 10"}}], "report": null}}

User: set volume to 35
{{"speech": "Volume at 35.", "actions": [{{"action": "pc_volume", "target": "set 35"}}], "report": null}}

User: mute
{{"speech": "Muted.", "actions": [{{"action": "pc_volume", "target": "mute"}}], "report": null}}

User: TV volume up 5
{{"speech": "TV volume up.", "actions": [{{"action": "tv", "target": "volume up 5"}}], "report": null}}

User: turn on the tv
{{"speech": "Waking the TV.", "actions": [{{"action": "tv", "target": "on"}}], "report": null}}

User: play Blinding Lights
{{"speech": "Playing it.", "actions": [{{"action": "spotify", "target": "play Blinding Lights"}}], "report": null}}

User: pause
{{"speech": "Paused.", "actions": [{{"action": "spotify", "target": "pause"}}], "report": null}}

User: do not disturb
{{"speech": "Going silent.", "actions": [{{"action": "set_mode", "target": "dnd"}}], "report": null}}

User: fun mode
{{"speech": "Fun mode â€” Spotify's going. What are we playing?", "actions": [{{"action": "set_mode", "target": "fun"}}], "report": null}}

User: study mode
{{"speech": "Study mode â€” Pomodoro on, no interruptions.", "actions": [{{"action": "set_mode", "target": "study"}}], "report": null}}

User: remind me to call mom in 30 minutes
{{"speech": "Reminder set.", "actions": [{{"action": "reminder", "target": "remind me to call mom in 30 minutes"}}], "report": null}}

User: morning briefing
{{"speech": "Running your briefing.", "actions": [{{"action": "briefing", "target": ""}}], "report": null}}

User: take me to the gym
{{"speech": "Directions up.", "actions": [{{"action": "directions", "target": "gym"}}], "report": null}}

User: what's in the news
{{"speech": "Here's what's happening.", "actions": [{{"action": "news", "target": ""}}], "report": null}}

User: add dentist appointment tomorrow at 3pm for an hour
{{"speech": "Dentist added.", "actions": [{{"action": "add_event", "target": "Dentist | 2026-05-01 15:00 | 60"}}], "report": null}}

User: open camera
{{"speech": "Camera's up.", "actions": [{{"action": "open_camera", "target": ""}}], "report": null}}

User: what do you see
{{"speech": "Looking.", "actions": [{{"action": "look", "target": ""}}], "report": null}}

User: build me a website for my coffee shop
{{"speech": "On it â€” about 30 seconds.", "actions": [{{"action": "build_website", "target": "coffee shop website with menu, story, gallery, and contact"}}], "report": null}}

User: make me a drummer website and open youtube
{{"speech": "Building the site and opening YouTube.", "actions": [{{"action": "build_website", "target": "drumming website with artist bio, tour dates, gallery, and listen section"}}, {{"action": "open_url", "target": "https://youtube.com"}}], "report": null}}

User: sign in to netflix and put on some music
{{"speech": "Signing into Netflix and starting Spotify.", "actions": [{{"action": "sign_in", "target": "netflix"}}, {{"action": "spotify", "target": "play"}}], "report": null}}

User: tile vs code and chrome
{{"speech": "Tiled.", "actions": [{{"action": "arrange_windows", "target": "Visual Studio Code|Chrome"}}], "report": null}}

User: put computer to sleep
{{"speech": "Sleeping.", "actions": [{{"action": "pc", "target": "sleep"}}], "report": null}}

User: set up my workday - open vs code and chrome, play my focus playlist, and set volume to 30
{{"speech": "Setting up your workday.", "mission": {{"title": "Workday setup", "steps": [{{"action": "open_app", "target": "visual studio code", "label": "Opening VS Code"}}, {{"action": "open_app", "target": "chrome", "label": "Opening Chrome"}}, {{"action": "spotify", "target": "play focus playlist", "label": "Starting focus playlist"}}, {{"action": "pc_volume", "target": "set 30", "label": "Setting volume to 30"}}]}}, "actions": [], "report": null}}

User: I'm getting ModuleNotFoundError: No module named requests
{{"speech": "On it — analyzing the error.", "actions": [{{"action": "fix_error", "target": "ModuleNotFoundError: No module named requests"}}], "report": null}}

User: there's an error on my screen, can you fix it
{{"speech": "Let me look at it.", "actions": [{{"action": "fix_error", "target": "error visible on screen"}}], "report": null}}

User: debug why my flask app crashes on startup, the project is on my desktop
{{"speech": "Digging in - I'll ask before I change anything.", "actions": [{{"action": "code_task", "target": "Debug why the Flask app on the desktop crashes on startup; find the project folder, read the traceback, fix the bug and verify"}}], "report": null}}

User: make the website darker and change the title to Roma Pizza
{{"speech": "Updating the site.", "actions": [{{"action": "edit_website", "target": "make the color scheme darker and change the main title to Roma Pizza"}}], "report": null}}

User: stop talking / voice off
{{"speech": "", "actions": [{{"action": "set_voice", "target": "off"}}], "report": null}}

User: tell me about newcastle united and their transfer window
{{"speech": "Checking the latest on that right now.", "actions": [{{"action": "web_research", "target": "Newcastle United latest transfer window news and signings"}}], "report": null}}

User: research the top 3 note-taking apps and build me a comparison website
{{"speech": "On it - I'll research them and build the site, and tell you when it's done.", "actions": [{{"action": "autonomous_task", "target": "Research the top 3 note-taking apps with current features and pricing, then build a comparison website from the findings"}}], "report": null}}

CRITICAL: Return ONE valid JSON object. Use "actions" array (or "mission" for 3+ steps). Nothing outside the JSON.\
"""


# â”€â”€ Sub-agent routing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Detects intent type and returns a specialized prompt suffix.
# Appended to the base system prompt to sharpen the brain for that domain.

_CODE_RE = re.compile(
    r"\b(code|coding|debug|error|bug|fix|function|class|script|python|javascript|"
    r"typescript|react|node|flask|django|api|import|syntax|compile|test|refactor|"
    r"terminal|git|commit|pull request|pr|deploy|docker|bash|powershell)\b",
    re.IGNORECASE,
)
_RESEARCH_RE = re.compile(
    r"\b(research|find out|look up|search|who is|what is|explain|how does|"
    r"summarize|article|news|paper|study|statistics|history|facts? about|"
    r"tell me about|give me info|information on|learn about)\b",
    re.IGNORECASE,
)
_CREATIVE_RE = re.compile(
    r"\b(write|draft|story|poem|script|email|message|post|caption|blog|essay|"
    r"slogan|tagline|brainstorm|ideas? for|creative|design|logo|brand|name for|"
    r"suggest|generate text|marketing|ad copy|description for)\b",
    re.IGNORECASE,
)

_SUB_CODE = """\
â”â” CODING AGENT MODE â”â”
You are now acting as a senior software engineer. Extra rules for this query:
â€¢ Read SCREEN context carefully â€” if an error message or code is visible, address it directly.
â€¢ Give concrete, copy-paste-ready code snippets when helpful (the TTS layer will skip code blocks).
â€¢ Diagnose root cause before suggesting a fix. Don't just say "try this" â€” explain why it works.
â€¢ If the active file is visible in context, reference it by name.
â€¢ Keep speech short (1â€“2 sentences), put detailed code/explanation in "report" field."""

_SUB_RESEARCH = """RESEARCH AGENT MODE - the user wants information:
- TIME-SENSITIVE topic (news, sports, transfers, prices, products, people in
  the news, anything that may have changed since training)? You MUST return
  {"action": "web_research", "target": "<the full question>"} with a short
  ack speech. NEVER answer these from memory - your training data is stale.
- Only answer directly from knowledge for STABLE facts: math, established
  history, definitions, how things work.
- When you do answer directly, lead with the most useful fact and be specific."""

_SUB_CREATIVE = """\
â”â” CREATIVE AGENT MODE â”â”
You are now acting as a creative director and copywriter. Extra rules for this query:
â€¢ Generate fresh, specific, high-quality content â€” not generic filler.
â€¢ For drafts/writing: put the full output in "report", summarize in speech ("Here's a draft â€” check the panel.").
â€¢ For ideas/brainstorming: give 3 concrete options in speech, more in report.
â€¢ Match tone to context â€” professional for emails, energetic for marketing, etc."""


def _route_to_subagent(user_input: str) -> str:
    """Returns a specialized sub-prompt suffix based on detected intent, or '' for general."""
    if _CODE_RE.search(user_input):
        return _SUB_CODE
    if _RESEARCH_RE.search(user_input):
        return _SUB_RESEARCH
    if _CREATIVE_RE.search(user_input):
        return _SUB_CREATIVE
    return ""


class GILBrain:
    def __init__(self, username: str):
        self.username = username
        # Seed with previous session so GIL remembers what you were working on
        try:
            from session_memory import load as _load_mem
            self.history: list[dict] = _load_mem()
        except Exception:
            self.history: list[dict] = []
        self.pending_results: list[str] = []

    def query(self, user_input: str, project_context: str = "",
              camera_state: str = "closed", _retry: int = 0,
              mode: str = "voice") -> dict:
        global _groq_key_index
        if _retry > 7:
            log.error("brain gave up after %d silent retries", _retry)
            return {"speech": "My AI services have been unreachable for a few minutes — I'll be back as soon as they are.",
                    "action": None, "target": None, "report": None}
        log.info("query: %s", user_input[:80])
        self.history.append({"role": "user", "content": user_input})

        now = datetime.now()
        hour = now.hour

        try:
            from credentials import list_services, get_credential
            services   = list_services()
            cred_lines = []
            for svc in services:
                cred = get_credential(svc)
                if cred:
                    cred_lines.append(f"  - {svc}: email/username = {cred[0]}")
            cred_block = (
                "STORED CREDENTIALS (use these for sign_in actions and when the user asks):\n"
                + ("\n".join(cred_lines) if cred_lines else "  (none stored yet)")
            )
        except Exception:
            cred_block = "STORED CREDENTIALS: (unavailable)"

        try:
            from memory import build_memory_context
            memory_context = build_memory_context(user_input)
        except Exception:
            memory_context = "LAST SESSION: No recent session on record."
        memory_context += _recall_past(user_input)

        # Screen context â€” use rich context_engine if available, fall back to screen.py
        try:
            from context_engine import get_screen_context, get_desktop_projects
            screen_context   = get_screen_context()
            projects         = get_desktop_projects()
            projects_context = ", ".join(projects) if projects else "None found."
        except Exception:
            try:
                from screen import get_screen_context, get_desktop_projects
                screen_context   = get_screen_context()
                projects         = get_desktop_projects()
                projects_context = ", ".join(projects) if projects else "None found."
            except Exception:
                screen_context   = ""
                projects_context = "Unavailable."

        # Goal context
        try:
            from goal_tracker import build_goal_context
            goal_context = build_goal_context()
        except Exception:
            goal_context = ""

        # Preference context
        try:
            from preferences import build_preference_context
            pref_context = build_preference_context()
        except Exception:
            pref_context = ""

        # Session context
        try:
            from session_manager import build_session_context
            session_context = build_session_context()
        except Exception:
            session_context = ""

        # Location context â€” cached, non-blocking
        try:
            from location import build_location_context
            location_context = build_location_context()
        except Exception:
            location_context = ""

        # Calendar context â€” today's events
        try:
            from gcalendar import build_calendar_context
            cal_context = build_calendar_context()
            if cal_context:
                location_context = (location_context + "\n" + cal_context).strip()
        except Exception:
            pass

        project_block = f"\nACTIVE PROJECT CONTEXT:\n{project_context}\n" if project_context else ""

        # Build extra context block for new engines
        results_ctx = ""
        if getattr(self, "pending_results", None):
            results_ctx = ("RECENT ACTION RESULTS (ground truth of what actually happened):\n"
                           + "\n".join(self.pending_results[-5:]))
            self.pending_results = []

        extra_blocks = []
        if results_ctx:
            extra_blocks.append(results_ctx)
        if goal_context:
            extra_blocks.append(goal_context)
        if pref_context:
            extra_blocks.append(pref_context)
        if session_context:
            extra_blocks.append(session_context)
        extra_str = ("\n\n" + "\n\n".join(extra_blocks)) if extra_blocks else ""

        # Cap each dynamic context block so the request can never blow past
        # Groq's payload limit (the 413 "Payload Too Large" bug). These are
        # background context, not the user's words, so truncation is safe.
        def _cap(s, n):
            s = s or ""
            return s if len(s) <= n else s[:n] + " …[trimmed]"
        memory_context   = _cap(memory_context, 3000)
        screen_context   = _cap(screen_context, 2000)
        projects_context = _cap(projects_context, 1500)
        cred_block       = _cap(cred_block, 1500)
        project_block    = _cap(project_block, 2500)
        extra_str        = _cap(extra_str, 2500)

        system = _SYSTEM.format(
            username=self.username,
            datetime=now.strftime("%A, %B %d %Y  %I:%M %p"),
            time_ex=now.strftime("%I:%M %p"),
            credentials=cred_block,
            memory_context=memory_context,
            user_profile=_load_user_profile(),
            screen_context=screen_context or "Not available.",
            projects_context=projects_context,
            location_context=location_context,
            camera_state=camera_state,
        ) + project_block + extra_str

        # Typed chat deserves full, rich answers - the 1-3 sentence rule
        # exists for spoken TTS replies only.
        if mode == "chat":
            system += (
                "\n\nCHAT MODE - the user is TYPING in the chat window right now.\n"
                "This OVERRIDES the 1-3 sentence speech rule:\n"
                "- Write a COMPLETE, generous answer like the best AI assistants: "
                "full paragraphs, markdown structure (## headings, **bold**, "
                "bullet lists), concrete facts, numbers, names and context.\n"
                "- For informational questions aim for 150-400+ words. "
                "Never a one-liner unless the user asked something trivial "
                "or you are just confirming an action.\n"
                "- The speech field IS the chat message - markdown is welcome there.\n"
                "- Task requests still return the same JSON with actions/mission.")

        # ── DELIVERABLE MODE — same manifesto the code agent uses (shared via
        # deliverable.py). When the user asks for a document/essay/report/deck/
        # code-review, the FULL finished asset goes in "report". ──
        content_request = _deliverable.is_content_task(user_input)
        if content_request:
            system += ("\n\n━━ DELIVERABLE / DOCUMENT REQUEST ━━\n"
                "The user is asking you to CREATE a document, essay, report, "
                "presentation or code-review. Put a SHORT spoken acknowledgement in "
                "\"speech\" and the COMPLETE, ready-to-submit deliverable (full "
                "markdown) in \"report\" — never truncated, never a stub.\n"
                + _deliverable.MANIFESTO)

        # Hard per-query language override — detected from the actual text,
        # not from the user profile. This overrides any "native: Hebrew" confusion.
        detected_lang = _detect_response_language(user_input)
        system += f"\n\nHARD LANGUAGE OVERRIDE: User just wrote in {detected_lang}. Your ENTIRE response MUST be in {detected_lang}. No exceptions."

        # Terse follow-up directive ("בעברית", "in Spanish", "translate that",
        # "make it shorter", "again") — the message carries almost no content of
        # its own; it asks GIL to RE-EXPRESS his previous answer. gpt-oss handled
        # this inconsistently (sometimes a bare "sure", sometimes an empty reply
        # → "Say that again?"), so we make the intent explicit against the history.
        if _is_followup_directive(user_input) and _last_assistant_answer(self.history):
            system += (
                "\n\nFOLLOW-UP: this short message is NOT a new topic — it asks you "
                "to redo YOUR PREVIOUS ANSWER with a change (a different language, "
                "shorter, more detail, etc.). Reproduce that previous answer IN FULL, "
                "transformed as asked. Do NOT merely acknowledge, and do NOT reply "
                "with an empty or one-line message.")

        # â”€â”€ Multi-agent routing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Detect intent type and append a specialized sub-prompt that sharpens
        # the brain's focus. This lets GIL behave like a specialist for that domain.
        _sub = _route_to_subagent(user_input)
        if _sub:
            system += "\n\n" + _sub

        chosen_model = _pick_model(user_input, _sub)
        if chosen_model != GROQ_MODEL:
            log.info("smart routing -> %s", chosen_model)

        # Any single stored history turn that ballooned (a huge pasted error,
        # research dump, etc.) is truncated so it can't inflate the request.
        for _m in self.history:
            if isinstance(_m.get("content"), str) and len(_m["content"]) > 4000:
                _m["content"] = _m["content"][:4000] + " …[trimmed]"

        payload = {
            "model":       chosen_model,
            "messages":    [{"role": "system", "content": system}] + self.history,
            "temperature": 0.30,
            # a document/essay/deck deliverable needs room to breathe (goes in
            # "report"); ordinary chat 2000, spoken commands 900. 3000 keeps the
            # whole request under gpt-oss's 8k-TPM ceiling.
            "max_tokens":  3000 if content_request else (2000 if mode == "chat" else 900),
        }
        # gpt-oss models spend part of the token budget on hidden reasoning
        # before emitting content. Keep effort low so fast commands stay snappy
        # and short (900-token) replies aren't starved by reasoning tokens.
        if "gpt-oss" in chosen_model:
            payload["reasoning_effort"] = "low"
        # Hard budget: if the whole request is still too big, drop the oldest
        # history turns until it fits (keeps the system prompt + newest turns).
        _MAX_CHARS = 90000
        def _payload_chars(p):
            return sum(len(m.get("content", "")) for m in p["messages"])
        while _payload_chars(payload) > _MAX_CHARS and len(payload["messages"]) > 2:
            payload["messages"].pop(1)   # oldest non-system message
            log.warning("trimming history to fit payload budget")

        def _try_groq(key: str):
            hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            return _session.post(GROQ_URL, json=payload, headers=hdrs, timeout=15)

        if not GROQ_KEYS:
            self.history.pop()
            return {"speech": "No Groq API key is configured — add GROQ_API_KEY to .env.",
                    "action": None, "target": None, "report": None}

        # _response_saved prevents double-pop when retrying on timeout
        _response_saved = False
        # Groq is the one and only brain (Anthropic removed — costs money).
        raw = ""
        try:
            if not raw:
                with _groq_key_lock:
                    _cur_idx = _groq_key_index
                resp = _try_groq(GROQ_KEYS[_cur_idx % len(GROQ_KEYS)])

                # Payload too large: shrink hard and retry (don't resend the
                # same oversized body — that's what made 413 fatal before).
                _shrink = 0
                while resp.status_code == 413 and _shrink < 3:
                    _shrink += 1
                    if len(payload["messages"]) > 2:
                        # keep system + only the newest 1-2 turns
                        payload["messages"] = ([payload["messages"][0]]
                                               + payload["messages"][-2:])
                    # and truncate what remains
                    for _m in payload["messages"]:
                        if len(_m.get("content", "")) > 6000:
                            _m["content"] = _m["content"][:6000] + " …[trimmed]"
                    payload["messages"][0]["content"] = \
                        payload["messages"][0]["content"][:12000]
                    log.warning("413 payload too large — shrink attempt %d", _shrink)
                    resp = _try_groq(GROQ_KEYS[_cur_idx % len(GROQ_KEYS)])

                if resp.status_code == 429:
                    with _groq_key_lock:
                        _groq_key_index += 1
                        _cur_idx = _groq_key_index
                    log.warning("Groq key rate-limited, rotating")
                    resp = _try_groq(GROQ_KEYS[_cur_idx % len(GROQ_KEYS)])

                if resp.status_code == 429:
                    log.warning("All Groq keys rate-limited — trying fallback models")
                    fallback_resp = None
                    for fb_model in _FALLBACK_MODELS:
                        for key in GROQ_KEYS:
                            fb_payload = {**payload, "model": fb_model}
                            # Llama/Gemma fallbacks reject reasoning_effort (400).
                            if "gpt-oss" not in fb_model:
                                fb_payload.pop("reasoning_effort", None)
                            hdrs = {"Authorization": f"Bearer {key}",
                                    "Content-Type": "application/json"}
                            try:
                                r = _session.post(GROQ_URL, json=fb_payload,
                                                  headers=hdrs, timeout=15)
                                if r.status_code != 429:
                                    fallback_resp = r
                                    log.info("Groq fallback succeeded on %s", fb_model)
                                    break
                            except Exception:
                                continue
                        if fallback_resp is not None:
                            break
                    if fallback_resp is None:
                        # Silent recovery: wait out the rate-limit window and
                        # retry — user sees the typing indicator, not an error.
                        if _retry < 6:
                            log.warning("rate-limited — silent retry %d in 22s", _retry + 1)
                            time.sleep(22)
                            if self.history and self.history[-1].get("role") == "user":
                                self.history.pop()
                            return self.query(user_input, project_context=project_context,
                                              camera_state=camera_state, _retry=_retry + 1)
                        return {"speech": "I'm being rate-limited hard right now — ask me again in a couple of minutes.",
                                "action": None, "target": None, "report": None}
                    resp = fallback_resp

                resp.raise_for_status()
                resp_json = resp.json()
                raw = resp_json["choices"][0]["message"]["content"].strip()
                # Track token usage for the context indicator
                usage = resp_json.get("usage", {})
                self.last_tokens_used   = usage.get("total_tokens", 0)
                self.last_prompt_tokens = usage.get("prompt_tokens", 0)

        except requests.exceptions.ConnectionError:
            log.error("No internet connection")
            if _retry < 6:
                import time as _t3; _t3.sleep(5 + _retry * 6)
                return self.query(user_input, project_context=project_context,
                                  camera_state=camera_state, _retry=_retry + 1,
                                  mode=mode)
            return _err("No internet connection — check your network.")
        except requests.exceptions.Timeout:
            log.warning("Groq timed out — retrying in 3 s")
            time.sleep(3)
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            _response_saved = True   # prevent double-pop in finally
            return self.query(user_input, project_context=project_context,
                              camera_state=camera_state, _retry=_retry + 1,
                              mode=mode)
        except Exception as exc:
            log.error("Brain error: %s", exc, exc_info=True)
            # Silent recovery: keep retrying quietly (typing indicator stays)
            # instead of surfacing "couldn't reach my AI service".
            if _retry < 6:
                import time as _t2; _t2.sleep(4 + _retry * 8)
                return self.query(user_input, project_context=project_context,
                                  camera_state=camera_state, _retry=_retry + 1,
                                  mode=mode)
            return {"speech": "My AI services have been unreachable for a few minutes — I'll be back as soon as they are.",
                    "action": None, "target": None, "report": None}
        finally:
            # Remove orphaned user message if no response was obtained
            if not _response_saved and not raw:
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()

        log.debug("raw response: %s", raw[:200])
        try:
            _payload_est = (len(system) + sum(len(m["content"]) for m in self.history)) // 4
            _health("ok", chosen_model, _payload_est)
        except Exception:
            pass
        parsed = _parse_json(raw)

        # A follow-up directive ("בעברית", "make it shorter") must not dead-end on
        # an empty reply — gpt-oss intermittently emits only hidden reasoning with
        # no content, which surfaced to the user as "Say that again?" and looked
        # like GIL forgetting the conversation. Retry once; the empty is transient.
        _empty = not (parsed.get("speech") or "").strip() \
            and not (parsed.get("report") or "").strip() \
            and not parsed.get("actions") and not parsed.get("action")
        if (_empty and _retry < 2
                and _is_followup_directive(user_input)
                and _last_assistant_answer(self.history[:-1])):
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()             # drop this turn so the retry re-adds it
            log.info("empty reply to a follow-up — retrying (%d)", _retry + 1)
            return self.query(user_input, project_context=project_context,
                              camera_state=camera_state, _retry=_retry + 1, mode=mode)

        # Store speech-only (not raw JSON) — cuts ~40% of every payload,
        # the root cause of the tokens-per-minute rate-limit failures.
        _speech_mem = parsed.get("speech") or raw[:200]
        self.history.append({"role": "assistant", "content": _speech_mem})

        if len(self.history) > 12:
            self.history = self.history[-12:]

        # Persist this task to memory â€” skip trivial replies and garbled/non-Latin STT
        try:
            if len(user_input.split()) > 4:
                _ascii_frac = sum(1 for c in user_input if ord(c) < 128) / max(len(user_input), 1)
                if _ascii_frac > 0.6:
                    clean_topic = _clean_task_topic(user_input)
                    from memory import record_task, record_schedule_activity
                    record_task(clean_topic, parsed.get("speech", ""))
                    record_schedule_activity(clean_topic)
        except Exception:
            pass

        return parsed

    def compact(self) -> dict:
        """Summarize the conversation history into a single condensed memory turn
        and drop the verbose frames — frees context and resets the gauge (Feature
        #2). Returns {ok, before, after, removed, summary, reason}."""
        hist = [m for m in self.history if isinstance(m.get("content"), str)]
        before_tok = sum(len(m["content"]) for m in hist) // 4
        if len(hist) < 4:
            return {"ok": False, "before": before_tok, "after": before_tok,
                    "removed": 0, "summary": "",
                    "reason": "The conversation is already compact — nothing to summarize yet."}
        transcript = "\n".join(
            f"{'You' if m['role'] == 'user' else 'GIL'}: {m['content']}" for m in hist)
        payload = {
            "model": GROQ_MODEL,
            "messages": [
                {"role": "system", "content":
                 "Condense this conversation into a tight briefing GIL can use to "
                 "keep going with full continuity. Capture what the user is working "
                 "on, decisions made, open threads, and any facts/names/paths that "
                 "matter. Terse third-person bullet-style prose, no preamble, under "
                 "200 words."},
                {"role": "user", "content": transcript[:12000]},
            ],
            "temperature": 0.3,
            "max_tokens": 400,
        }
        if "gpt-oss" in GROQ_MODEL:
            payload["reasoning_effort"] = "low"
        summary = ""
        for key in GROQ_KEYS:
            try:
                hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                r = _session.post(GROQ_URL, json=payload, headers=hdrs, timeout=20)
                r.raise_for_status()
                summary = (r.json()["choices"][0]["message"]["content"] or "").strip()
                if summary:
                    break
            except Exception:
                continue
        # Only commit the summary if it actually shrinks the context — for a short
        # conversation the summary can be longer than the original, which would
        # inflate context instead of freeing it.
        if summary:
            summ_hist = [{"role": "assistant",
                          "content": "[EARLIER CONVERSATION — condensed]\n" + summary}]
            summ_tok = sum(len(m["content"]) for m in summ_hist) // 4
            if summ_tok < before_tok:
                self.history = summ_hist
                try:
                    from session_memory import save as _save_mem
                    _save_mem(self.history)
                except Exception:
                    pass
                return {"ok": True, "before": before_tok, "after": summ_tok,
                        "removed": len(hist), "summary": summary}
        # Summary was empty or no smaller than the original: fall back to dropping
        # the oldest turns, but only if that genuinely reduces the context.
        trimmed = hist[-4:]
        trim_tok = sum(len(m["content"]) for m in trimmed) // 4
        if trim_tok < before_tok:
            self.history = trimmed
            reason = ("Trimmed to the latest turns (the summary service was unreachable)."
                      if not summary else
                      "Kept the latest turns — a summary wouldn't have been any smaller.")
            return {"ok": True, "before": before_tok, "after": trim_tok,
                    "removed": len(hist) - len(trimmed), "summary": "", "reason": reason}
        return {"ok": False, "before": before_tok, "after": before_tok,
                "removed": 0, "summary": "",
                "reason": "The conversation is already compact — nothing to free up yet."}

    def proactive_query(self) -> dict | None:
        """
        Call this from a background timer (e.g. every 5â€“10 minutes) to let G.I.L.
        proactively notice something and speak without being prompted.
        Returns None if G.I.L. has nothing useful to add right now.

        Example usage in main.py:
            import threading
            def _proactive_loop():
                while True:
                    time.sleep(600)  # 10 minutes
                    result = brain.proactive_query()
                    if result and result.get("speech"):
                        from voice import speak
                        speak(result["speech"])
            threading.Thread(target=_proactive_loop, daemon=True).start()
        """
        prompt = (
            "[PROACTIVE â€” do not announce you are doing a proactive check] "
            f"Based on the current screen context, time of day, memory, and what {self.username} "
            "has been doing: is there something genuinely useful you can say right now â€” "
            "a suggestion, observation, warning, or question? "
            "Only speak if you have something specific and actionable. "
            'If not, return: {"speech": "", "action": null, "target": null, "report": null}'
        )
        result = self.query(prompt)
        if not result.get("speech", "").strip():
            return None
        return result

    def plan_next_step(self, goal: str, history: list) -> dict:
        """
        Autonomous loop planner. Given the overall goal and everything done
        so far, decide the SINGLE next action — or declare the goal complete.
        Returns:
          {"complete": true,  "summary": "..."}                  when done
          {"complete": false, "action": "...", "target": "...",
           "reason": "short present-tense reason"}               otherwise
        Grounded in real step results; the 70B model keeps it on-task.
        """
        done_block = "\n".join(
            f"{i}. {h['action']}({h.get('target','')[:50]}) -> "
            f"{str(h.get('result',''))[:180]}"
            for i, h in enumerate(history, 1)) or "(nothing done yet)"

        actions_ref = _extract_action_vocab(_SYSTEM)
        sys = (
            "You are G.I.L. running an AUTONOMOUS task loop. Work toward the "
            "GOAL one step at a time until it is genuinely, fully complete. "
            "After each step you see its real result.\n\n"
            "Return ONE JSON object, nothing else:\n"
            '- To continue: {"complete": false, "action": "<name>", '
            '"target": "<arg>", "reason": "<short reason the user sees>"}\n'
            '- When the goal is fully achieved: {"complete": true, '
            '"summary": "<what was accomplished>"}\n\n'
            "Rules:\n"
            "- Pick the smallest useful next action. Don't repeat a step that "
            "already succeeded.\n"
            "- If a step failed, adapt — try a different approach, don't loop.\n"
            "- Be honest: only say complete when the GOAL is actually met.\n"
            "- Use only these actions:\n" + actions_ref)
        usr = f"GOAL: {goal}\n\nSTEPS DONE SO FAR:\n{done_block}\n\nNext step?"

        payload = {"model": _SMART_MODEL,
                   "messages": [{"role": "system", "content": sys},
                                {"role": "user", "content": usr}],
                   "temperature": 0.2, "max_tokens": 300}
        for model in (_SMART_MODEL, "llama-3.1-8b-instant"):
            payload["model"] = model
            for key in GROQ_KEYS:
                try:
                    r = _session.post(GROQ_URL, json=payload,
                                      headers={"Authorization": f"Bearer {key}"},
                                      timeout=25)
                    if r.status_code == 429:
                        continue
                    r.raise_for_status()
                    raw = r.json()["choices"][0]["message"]["content"]
                    m = re.search(r"\{[\s\S]*\}", raw)
                    if m:
                        return json.loads(m.group(0))
                except Exception as exc:
                    log.debug("plan_next_step attempt failed: %s", exc)
        # Unreachable → stop cleanly rather than spin
        return {"complete": True,
                "summary": "I couldn't reach my planner, so I stopped here."}

    def summarize_results(self, title: str, results: list) -> str:
        """
        One short spoken summary of a completed mission.
        results = [(label, status, detail), ...]  status: "done"/"fail"
        Cheap single call on the fast model, no history. Falls back to a
        counted summary if the API is unreachable.
        """
        ok   = sum(1 for _, s, _ in results if s == "done")
        n    = len(results)
        fallback = (f"Mission complete — all {n} steps done." if ok == n
                    else f"Mission finished — {ok} of {n} steps succeeded.")
        if not GROQ_KEYS:
            return fallback
        lines = "\n".join(
            f"- {label}: {status.upper()}" + (f" — {detail[:100]}" if detail else "")
            for label, status, detail in results
        )
        payload = {
            "model": "llama-3.1-8b-instant",
            "messages": [
                {"role": "system", "content":
                 "You are G.I.L., a JARVIS-style assistant. Summarize the mission "
                 "outcome for the user in 1-2 short spoken sentences. Confident, "
                 "specific, no markdown, no lists. Mention failures plainly."},
                {"role": "user", "content": f"Mission: {title}\nSteps:\n{lines}"},
            ],
            "temperature": 0.4,
            "max_tokens": 120,
        }
        try:
            hdrs = {"Authorization": f"Bearer {GROQ_KEYS[0]}",
                    "Content-Type": "application/json"}
            r = _session.post(GROQ_URL, json=payload, headers=hdrs, timeout=10)
            r.raise_for_status()
            out = r.json()["choices"][0]["message"]["content"].strip()
            return out or fallback
        except Exception:
            return fallback


def _parse_json(raw: str) -> dict:
    cleaned = re.sub(r"```(?:json)?|```", "", raw).strip()

    parsed = None
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    if parsed is None:
        start = cleaned.find("{")
        if start != -1:
            depth = 0
            for i, ch in enumerate(cleaned[start:], start):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            parsed = json.loads(cleaned[start:i + 1])
                        except json.JSONDecodeError:
                            pass
                        break

    if parsed is None:
        m = re.search(r'"speech"\s*:\s*"([^"]+)"', cleaned)
        # Salvage a (possibly truncated) document body so a long "report" survives a
        # parse failure instead of being silently dropped to speech-only.
        report = None
        rm = re.search(r'"report"\s*:\s*"(.*)$', cleaned, re.DOTALL)
        if rm:
            rt = re.sub(r'(?:\\?"\s*[},\]]*\s*)$', '', rm.group(1).rstrip())
            report = rt.replace("\\n", "\n").replace("\\t", "\t").replace('\\"', '"') or None
        if m or report:
            print("[G.I.L. BRAIN] Partial parse â€” salvaged speech/report.")
            return {"speech": (m.group(1) if m else ""), "action": None,
                    "target": None, "report": report, "extra_actions": []}
        log.error("JSON parse failed. Raw: %s", raw[:120])
        return {"speech": "", "action": None, "target": None, "report": None, "extra_actions": []}

    # Normalise new "actions" array format â†’ legacy action/target + extra_actions
    actions = parsed.get("actions") or []
    if actions:
        first = actions[0] if actions else {}
        parsed.setdefault("action", first.get("action"))
        parsed.setdefault("target", first.get("target") or "")
        parsed["extra_actions"] = actions[1:]
    else:
        parsed.setdefault("action", parsed.get("action"))
        parsed.setdefault("target", parsed.get("target") or "")
        parsed["extra_actions"] = []

    return parsed


def _health(status: str, model: str, tokens: int = 0, note: str = "") -> None:
    """One line per brain request → data/brain_health.log (proves failures)."""
    try:
        from pathlib import Path as _P
        p = _P(__file__).parent / "data" / "brain_health.log"
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%m-%d %H:%M:%S} {status:8s} {model:28s} "
                    f"~{tokens}tok {note[:80]}\n")
    except Exception:
        pass


def _err(msg: str) -> dict:
    log.debug("silent error: %s", msg)
    return {"speech": "", "action": None, "target": None, "report": None}

