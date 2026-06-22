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
            return json.load(f).get("ai_model", "llama-3.1-8b-instant")
    except Exception:
        return "llama-3.1-8b-instant"


GROQ_MODEL = _load_model()

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
â€¢ Respond in {username}'s language. If they write Hebrew, reply Hebrew (but keep JSON keys in English).

â”â” MULTI-TASK â€” CRITICAL â”â”
When the user asks for multiple things in one message, return ALL of them.
Use the "actions" array â€” include EVERY action needed:
{{"speech": "...", "actions": [{{"action": "...", "target": "..."}}, {{"action": "...", "target": "..."}}], "report": null}}
Single task: {{"speech": "...", "actions": [{{"action": "...", "target": "..."}}], "report": null}}
No action needed: {{"speech": "...", "actions": [], "report": null}}

â”â” INTELLIGENCE RULES â”â”
â€¢ CONTEXT: Read screen context, active projects, memory, calendar â€” reference them naturally.
â€¢ MEMORY: When RELEVANT MEMORIES appear â€” weave them in. Never say "I don't have memory."
â€¢ JUDGMENT: Challenge flawed ideas in one sentence. Question destructive actions before executing.
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
open_app â†’ app name | open_url â†’ full URL | web_search â†’ query string | web_research â†’ question (fetches live results and speaks a summary â€” use instead of web_search when user wants an answer spoken aloud)
system_vitals â†’ null | take_screenshot â†’ null | identify_song â†’ null
sign_in â†’ service | save_credential â†’ "svc|email|pw" | delete_credential â†’ svc | list_credentials â†’ null
show_settings â†’ null | note â†’ text | list_notes â†’ null | clip_history â†’ null
create_project â†’ name | add_task â†’ "task|project" | complete_task â†’ task | list_tasks â†’ null
build â†’ "description|folder-name" (ONLY for code/software projects, NEVER websites)
open_terminal â†’ command | prompt_project â†’ "folder|task"
focus_window â†’ app | arrange_windows â†’ "app1|app2" | close_window â†’ app
minimize_all â†’ null | maximize_window â†’ app
open_file â†’ path | read_file â†’ path | list_directory â†’ path | find_file â†’ filename
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

CRITICAL: Return ONE valid JSON object. Use "actions" array always. Nothing outside the JSON.\
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

_SUB_RESEARCH = """\
â”â” RESEARCH AGENT MODE â”â”
You are now acting as a research analyst. Extra rules for this query:
â€¢ Lead with the single most useful fact in your speech (1â€“2 sentences).
â€¢ Put deeper detail, sources, or structured breakdown in the "report" field.
â€¢ If you know the topic well, be specific â€” cite numbers, names, dates where relevant.
â€¢ Don't hedge with "I'm not sure" unless genuinely uncertain. Be direct."""

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

    def query(self, user_input: str, project_context: str = "",
              camera_state: str = "closed", _retry: int = 0) -> dict:
        global _groq_key_index
        if _retry > 2:
            log.error("Groq timed out 3 times — giving up")
            return {"speech": "My connection to Groq keeps timing out. Check your internet and try again.",
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
        extra_blocks = []
        if goal_context:
            extra_blocks.append(goal_context)
        if pref_context:
            extra_blocks.append(pref_context)
        if session_context:
            extra_blocks.append(session_context)
        extra_str = ("\n\n" + "\n\n".join(extra_blocks)) if extra_blocks else ""

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

        # â”€â”€ Multi-agent routing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        # Detect intent type and append a specialized sub-prompt that sharpens
        # the brain's focus. This lets GIL behave like a specialist for that domain.
        _sub = _route_to_subagent(user_input)
        if _sub:
            system += "\n\n" + _sub

        payload = {
            "model":       GROQ_MODEL,
            "messages":    [{"role": "system", "content": system}] + self.history,
            "temperature": 0.30,
            "max_tokens":  500,
        }
        def _try_groq(key: str):
            hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            return _session.post(GROQ_URL, json=payload, headers=hdrs, timeout=15)

        if not GROQ_KEYS:
            self.history.pop()
            return {"speech": "No Groq API key is configured — check your .env file.",
                    "action": None, "target": None, "report": None}

        # _response_saved prevents double-pop when retrying on timeout
        _response_saved = False
        raw = ""
        try:
            with _groq_key_lock:
                _cur_idx = _groq_key_index
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
                    log.warning("All Groq models rate-limited")
                    return {"speech": "Rate-limited — give me 30 seconds and ask again.",
                            "action": None, "target": None, "report": None}
                resp = fallback_resp

            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()

        except requests.exceptions.ConnectionError:
            log.error("No internet connection")
            return _err("No connection. Check your internet.")
        except requests.exceptions.Timeout:
            log.warning("Groq timed out — retrying in 3 s")
            time.sleep(3)
            if self.history and self.history[-1].get("role") == "user":
                self.history.pop()
            _response_saved = True   # prevent double-pop in finally
            return self.query(user_input, project_context=project_context,
                              camera_state=camera_state, _retry=_retry + 1)
        except Exception as exc:
            log.error("Brain error: %s", exc, exc_info=True)
            return {"speech": "Groq is unavailable right now. Try again in a moment.",
                    "action": None, "target": None, "report": None}
        finally:
            # Remove orphaned user message if no response was obtained
            if not _response_saved and not raw:
                if self.history and self.history[-1].get("role") == "user":
                    self.history.pop()

        log.debug("raw response: %s", raw[:200])
        parsed = _parse_json(raw)
        self.history.append({"role": "assistant", "content": raw})

        if len(self.history) > 40:
            self.history = self.history[-40:]

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
        if m:
            print("[G.I.L. BRAIN] Partial parse â€” extracted speech only.")
            return {"speech": m.group(1), "action": None, "target": None, "report": None, "extra_actions": []}
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


def _err(msg: str) -> dict:
    log.debug("silent error: %s", msg)
    return {"speech": "", "action": None, "target": None, "report": None}

