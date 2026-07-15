"""
study_coach.py — G.I.L. interactive study course builder.

Given an exam (and what the user already knows), GIL:
  1. RESEARCHES what the exam actually covers (live, via research.py).
  2. Designs a Duolingo-style COURSE: units → levels, each level a short quiz
     of escalating difficulty, tailored to the user's background.
  3. Renders it into a polished, self-contained interactive web app (a real
     learning path with XP, streaks, progress saved locally, instant feedback)
     and opens it in the browser.

Entry point: build_course(exam, background) -> (chat_summary, saved_path)
Triggered by the /study command and the study_prep brain action.
"""

import json
import os
import re
from datetime import datetime
from pathlib import Path

from logger import get as _get_log

log = _get_log("study")

_OUT_DIR  = Path.home() / "Documents" / "GIL_Study"
_TEMPLATE = Path(__file__).parent / "study_app" / "course_template.html"


def _groq_json(messages: list, max_tokens: int = 3000) -> dict | None:
    """Call Groq and parse the first JSON object out of the reply."""
    import requests
    keys = [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]
    for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
        for key in keys:
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": model, "messages": messages,
                          "temperature": 0.4, "max_tokens": max_tokens,
                          "response_format": {"type": "json_object"}},
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    timeout=60)
                if r.status_code == 429:
                    continue
                r.raise_for_status()
                raw = r.json()["choices"][0]["message"]["content"]
                m = re.search(r"\{[\s\S]*\}", raw)
                return json.loads(m.group(0)) if m else None
            except Exception as exc:
                log.debug("study json attempt failed: %s", exc)
    return None


def _act(kind, label):
    try:
        import activity
        return activity.start(kind, label)
    except Exception:
        return None


def _act_done(aid, detail=""):
    try:
        import activity
        if aid is not None:
            activity.done(aid, detail[:100])
    except Exception:
        pass


def _research_scope(exam: str) -> str:
    try:
        import research
        md, _ = research.run(f"What topics, skills and question types are on "
                             f"the {exam}? What must a candidate study?")
        return md[:3500]
    except Exception as exc:
        log.warning("scope research failed: %s", exc)
        return ""


_COMMON = ("You are G.I.L., an expert exam coach building a Duolingo-style "
           "course. Be concrete, high-level and accurate — real concepts, "
           "real questions, real worked answers. Never vague.")


def _build_outline(exam: str, bg: str, scope: str) -> dict | None:
    return _groq_json([
        {"role": "system", "content": _COMMON},
        {"role": "user", "content":
         f"EXAM: {exam}\nLEARNER ALREADY KNOWS: {bg}\n"
         + (f"RESEARCHED SCOPE:\n{scope}\n" if scope else "")
         + "Design the course outline as JSON:\n"
         '{"title": "...", "subtitle": "one motivating line", '
         '"units": [{"title": "...", "desc": "one line"}]}\n'
         "4–6 units, ordered from the learner's current level up to exam level. "
         "Each unit = a coherent theme. JSON only."}],
        max_tokens=700)


def _build_unit_levels(exam: str, bg: str, unit: dict, scope: str) -> list:
    data = _groq_json([
        {"role": "system", "content": _COMMON},
        {"role": "user", "content":
         f"EXAM: {exam}\nLEARNER KNOWS: {bg}\n"
         f"UNIT: {unit.get('title')} — {unit.get('desc','')}\n"
         + (f"CONTEXT:\n{scope[:1500]}\n" if scope else "")
         + "Create the levels for THIS unit as JSON:\n"
         '{"levels": [{"title": "...", "questions": ['
         '{"q": "question text (use ``` for code)", "type": "mc", '
         '"options": ["a","b","c","d"], "answer": 0, "explain": "why"}, '
         '{"q": "...", "type": "input", "answer": "42", "explain": "..."}]}]}\n'
         "3–4 levels, each with 5–6 questions, difficulty rising within and "
         "across levels. Mix multiple-choice (\"mc\", answer = index of correct "
         "option) and short \"input\" questions. Include real problem-solving "
         "and, where relevant, small code/logic questions. Every question needs "
         "a correct answer and a one-sentence explanation. JSON only."}],
        max_tokens=3200)
    levels = (data or {}).get("levels") or []
    # sanitize
    clean = []
    for lv in levels:
        qs = []
        for q in (lv.get("questions") or []):
            if not q.get("q"):
                continue
            if q.get("type") == "input" or not q.get("options"):
                q["type"] = "input"
                q.setdefault("answer", "")
            else:
                q["type"] = "mc"
                try:
                    q["answer"] = int(q.get("answer", 0))
                except Exception:
                    q["answer"] = 0
                q["answer"] = max(0, min(q["answer"], len(q["options"]) - 1))
            q.setdefault("explain", "")
            qs.append(q)
        if qs:
            clean.append({"title": lv.get("title", "Level"), "questions": qs})
    return clean


def build_course(exam: str, background: str = "") -> tuple[str, "Path | None"]:
    """Full interactive-course pipeline. Returns (chat_summary, saved_path)."""
    exam = (exam or "").strip()
    if not exam:
        return ("Which exam should I build a course for? Tell me its name and "
                "what you already know (e.g. `/study cyber exam | I know Java`).",
                None)
    bg = background.strip() or "a motivated beginner"

    try:
        import activity
        activity.set_group(f"Study course: {exam[:38]}")
    except Exception:
        pass

    aid = _act("search", f"Researching what the {exam[:36]} covers")
    scope = _research_scope(exam)
    _act_done(aid, "scope gathered" if scope else "using general knowledge")

    aid = _act("brain", "Designing the course structure")
    outline = _build_outline(exam, bg, scope)
    _act_done(aid)
    if not outline or not outline.get("units"):
        return ("I couldn't design the course right now (my models are busy) — "
                "try again in a minute.", None)

    course = {"title": outline.get("title") or f"{exam} — Study Course",
              "subtitle": outline.get("subtitle") or f"Tailored for: {bg}",
              "units": []}
    units = outline["units"][:6]
    for i, u in enumerate(units, 1):
        aid = _act("note", f"Building unit {i}/{len(units)}: {u.get('title','')[:30]}")
        levels = _build_unit_levels(exam, bg, u, scope)
        _act_done(aid, f"{len(levels)} levels")
        if levels:
            course["units"].append({"title": u.get("title", f"Unit {i}"),
                                    "desc": u.get("desc", ""), "levels": levels})

    try:
        import activity
        activity.clear_group()
    except Exception:
        pass

    n_levels = sum(len(u["levels"]) for u in course["units"])
    n_q = sum(len(l["questions"]) for u in course["units"] for l in u["levels"])
    if not n_levels:
        return ("The course came back empty — my models are likely rate-limited. "
                "Try again shortly.", None)

    # Render into the interactive app
    saved = None
    try:
        template = _TEMPLATE.read_text(encoding="utf-8")
        injected = re.sub(r"/\*__COURSE__\*/[\s\S]*?/\*__END__\*/",
                          "/*__COURSE__*/ " + json.dumps(course, ensure_ascii=False)
                          + " /*__END__*/", template, count=1)
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", exam.lower()).strip("_")[:40] or "course"
        saved = _OUT_DIR / f"{slug}_course.html"
        saved.write_text(injected, encoding="utf-8")
        try:
            os.startfile(str(saved))
        except Exception:
            import webbrowser
            webbrowser.open(saved.as_uri())
    except Exception as exc:
        log.error("could not render study course: %s", exc, exc_info=True)
        return (f"I built the course ({n_levels} levels) but couldn't open it — "
                f"{exc}.", None)

    unit_list = "\n".join(f"{i}. **{u['title']}** — {len(u['levels'])} levels"
                          for i, u in enumerate(course["units"], 1))
    summary = (f"Your interactive **{course['title']}** is ready and open in "
               f"your browser — a Duolingo-style course with **{n_levels} levels** "
               f"and **{n_q} questions**, tailored to: {bg}.\n\n"
               f"Work through the path level by level — each has instant feedback, "
               f"XP and streaks, and your progress saves automatically.\n\n"
               f"**Units:**\n{unit_list}\n\n"
               f"Ask me to add harder levels or a new topic anytime.")
    return summary, saved


# Back-compat: the study_prep action calls prepare(); keep it pointing here.
def prepare(exam: str, background: str = "") -> tuple[str, "Path | None"]:
    return build_course(exam, background)
