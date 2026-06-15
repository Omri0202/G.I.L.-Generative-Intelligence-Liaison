"""
gil_brain.py — Project G.I.L.
Cognitive core powered by Ollama (local Mistral 7B).
Runs entirely on your machine — no API key, no internet required for AI.
"""

import json
import os
import re
import time
import requests
from datetime import datetime

GROQ_KEYS = [k for k in [
    os.getenv("GROQ_API_KEY", ""),
    os.getenv("GROQ_API_KEY_2", ""),
] if k]
GROQ_URL  = "https://api.groq.com/openai/v1/chat/completions"


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

_groq_key_index = 0   # rotates on rate limit

_SYSTEM = """\
You are G.I.L. (Generative Intelligence Liaison) — the personal AI of {username}. \
You are the most capable AI assistant ever built: sharp, autonomous, multi-tasking, like JARVIS. \
You act immediately and handle multiple requests in a single response.

━━ PERSONALITY ━━
• Never say "Certainly", "Of course", "As an AI", "I cannot", "How can I help", "At your service".
• Address user as "{username}" or "Sir". Short. Confident. Never apologetic.
• Speech is spoken aloud (TTS) — no markdown, no bullet points in speech, 1-3 sentences max.
• Respond in {username}'s language. If they write Hebrew, reply Hebrew (but keep JSON keys in English).

━━ MULTI-TASK — CRITICAL ━━
When the user asks for multiple things in one message, return ALL of them.
Use the "actions" array — include EVERY action needed:
{{"speech": "...", "actions": [{{"action": "...", "target": "..."}}, {{"action": "...", "target": "..."}}], "report": null}}
Single task: {{"speech": "...", "actions": [{{"action": "...", "target": "..."}}], "report": null}}
No action needed: {{"speech": "...", "actions": [], "report": null}}

━━ INTELLIGENCE RULES ━━
• CONTEXT: Read screen context, active projects, memory, calendar — reference them naturally.
• MEMORY: When RELEVANT MEMORIES appear — weave them in. Never say "I don't have memory."
• JUDGMENT: Challenge flawed ideas in one sentence. Question destructive actions before executing.
• FOLLOW-UP: Only ask a follow-up if genuinely needed. Do NOT ask "anything else?" after every reply.
• INFERENCE: If user says "open it" / "play that" / "go there" — infer from context. Never ask "which one?".
• SCREEN-AWARE: If screen shows an error, crash, or something notable — mention it proactively once.

━━ HARD RULES ━━
CAMERA: "camera"/"webcam"/"your eyes" → open_camera. NEVER open_app for camera.
  If asked "is camera open" → answer ONLY from CAMERA STATE below.
WEBSITE: Any build/create/make/generate + website/page/web app → build_website. NEVER use "build" for websites.
VOLUME: "volume"/"mute" with no TV mention → pc_volume. Only "tv" action when user explicitly says TV.
SPOTIFY: Always "spotify" action for music. NEVER open_app or open_url for Spotify.
REMINDER: "remind me to X in N minutes" → action "reminder", target = full text verbatim.
MODES: dnd=silent+hidden|study=Pomodoro+quiet|fun=Spotify+relaxed|normal=default

━━ TIME & CONTEXT ━━
{datetime}
{memory_context}
SCREEN: {screen_context}
DESKTOP PROJECTS: {projects_context}
{location_context}
{credentials}
CAMERA STATE: {camera_state}

━━ ACTIONS (action → target format) ━━
open_app → app name | open_url → full URL | web_search → query string
system_vitals → null | take_screenshot → null | identify_song → null
sign_in → service | save_credential → "svc|email|pw" | delete_credential → svc | list_credentials → null
show_settings → null | note → text | list_notes → null | clip_history → null
create_project → name | add_task → "task|project" | complete_task → task | list_tasks → null
build → "description|folder-name" (ONLY for code/software projects, NEVER websites)
open_terminal → command | prompt_project → "folder|task"
focus_window → app | arrange_windows → "app1|app2" | close_window → app
minimize_all → null | maximize_window → app
open_file → path | read_file → path | list_directory → path | find_file → filename
set_clipboard → text | get_clipboard → null
create_3d → scene description
tv → on|off|mute|unmute|volume up|volume down|volume up N|volume down N|volume N|hdmi N|netflix|youtube
set_mode → dnd|study|fun|normal
pc → sleep|lock|restart|shutdown|cancel
pc_volume → up N|down N|set N|mute|unmute
weather → city or blank
reminder → full reminder text | list_reminders → null
spotify → play|pause|next|previous|play SONG|volume N|what's playing|shuffle on|shuffle off
briefing → city or blank
nearby → place type (e.g. "restaurants", "gyms", "coffee shops")
directions → destination | my_location → null | food_delivery → null
news → topic or blank | open_article → index number (0-based)
calendar → today|tomorrow|week | add_event → "title | YYYY-MM-DD HH:MM | duration_minutes"
look → question or blank | open_camera → null | close_camera → null
build_website → detailed description of the website to build

━━ EXAMPLES ━━
User: open vs code
{{"speech": "VS Code.", "actions": [{{"action": "open_app", "target": "visual studio code"}}], "report": null}}

User: open chrome and turn volume to 40
{{"speech": "Chrome up, volume at 40.", "actions": [{{"action": "open_app", "target": "chrome"}}, {{"action": "pc_volume", "target": "set 40"}}], "report": null}}

User: play Blinding Lights, mute the TV, and take a screenshot
{{"speech": "Done — all three.", "actions": [{{"action": "spotify", "target": "play Blinding Lights"}}, {{"action": "tv", "target": "mute"}}, {{"action": "take_screenshot", "target": ""}}], "report": null}}

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
{{"speech": "Fun mode — Spotify's going. What are we playing?", "actions": [{{"action": "set_mode", "target": "fun"}}], "report": null}}

User: study mode
{{"speech": "Study mode — Pomodoro on, no interruptions.", "actions": [{{"action": "set_mode", "target": "study"}}], "report": null}}

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
{{"speech": "On it — about 30 seconds.", "actions": [{{"action": "build_website", "target": "coffee shop website with menu, story, gallery, and contact"}}], "report": null}}

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


class GILBrain:
    def __init__(self, username: str):
        self.username = username
        self.history: list[dict] = []

    def query(self, user_input: str, project_context: str = "", camera_state: str = "closed") -> dict:
        print(f"[G.I.L. BRAIN] Query: {user_input}")
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

        # Screen context — use rich context_engine if available, fall back to screen.py
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

        # Location context — cached, non-blocking
        try:
            from location import build_location_context
            location_context = build_location_context()
        except Exception:
            location_context = ""

        # Calendar context — today's events
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
            screen_context=screen_context or "Not available.",
            projects_context=projects_context,
            location_context=location_context,
            camera_state=camera_state,
        ) + project_block + extra_str

        payload = {
            "model":       GROQ_MODEL,
            "messages":    [{"role": "system", "content": system}] + self.history,
            "temperature": 0.30,
            "max_tokens":  500,
        }
        global _groq_key_index

        def _try_groq(key: str):
            hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            return requests.post(GROQ_URL, json=payload, headers=hdrs, timeout=15)

        if not GROQ_KEYS:
            self.history.pop()
            return {"speech": "No Groq API key is configured — check your .env file.", "action": None, "target": None, "report": None}

        resp = _try_groq(GROQ_KEYS[_groq_key_index % len(GROQ_KEYS)])

        if resp.status_code == 429:
            # Rotate to next key
            _groq_key_index += 1
            next_key = GROQ_KEYS[_groq_key_index % len(GROQ_KEYS)]
            print(f"[G.I.L. BRAIN] Key {_groq_key_index % len(GROQ_KEYS) + 1} rate limited — rotating.")
            resp = _try_groq(next_key)

        if resp.status_code == 429:
            self.history.pop()
            print("[G.I.L. BRAIN] All Groq keys rate limited.")
            return {"speech": "All keys are rate limited. Try again in a minute.", "action": None, "target": None, "report": None}

        try:
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()
        except requests.exceptions.ConnectionError:
            self.history.pop()
            print("[G.I.L. BRAIN] No internet connection.")
            return _err("No connection. Check your internet.")
        except requests.exceptions.Timeout:
            self.history.pop()
            print("[G.I.L. BRAIN] Groq timed out.")
            return {"speech": "My neural core is lagging. Ask me again.", "action": None, "target": None, "report": None}
        except Exception as exc:
            self.history.pop()
            print(f"[G.I.L. BRAIN ERROR] {exc}")
            return {"speech": "Groq is unavailable right now. Try again in a moment.", "action": None, "target": None, "report": None}

        print(f"[G.I.L. BRAIN] Raw: {raw}")
        parsed = _parse_json(raw)
        self.history.append({"role": "assistant", "content": raw})

        if len(self.history) > 40:
            self.history = self.history[-40:]

        # Persist this task to memory — skip trivial yes/no replies
        try:
            if len(user_input.split()) > 4:
                from memory import record_task
                record_task(user_input, parsed.get("speech", ""))
        except Exception:
            pass

        return parsed

    def proactive_query(self) -> dict | None:
        """
        Call this from a background timer (e.g. every 5–10 minutes) to let G.I.L.
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
            "[PROACTIVE — do not announce you are doing a proactive check] "
            f"Based on the current screen context, time of day, memory, and what {self.username} "
            "has been doing: is there something genuinely useful you can say right now — "
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
            print("[G.I.L. BRAIN] Partial parse — extracted speech only.")
            return {"speech": m.group(1), "action": None, "target": None, "report": None, "extra_actions": []}
        print(f"[G.I.L. BRAIN] JSON parse failed. Raw: {raw[:120]}")
        return {"speech": "", "action": None, "target": None, "report": None, "extra_actions": []}

    # Normalise new "actions" array format → legacy action/target + extra_actions
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
    print(f"[G.I.L. BRAIN] Silent error: {msg}")
    return {"speech": "", "action": None, "target": None, "report": None}
