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
    for model in ("qwen/qwen3.8-27b", "openai/gpt-oss-20b"):
        for key in keys:
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": model, "messages": messages,
                          "temperature": 0.4, "max_tokens": max_tokens,
                          "response_format": {"type": "json_object"},
                          **({"reasoning_effort": "low"} if "gpt-oss" in model else {})},
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


def _lang_rule(language: str) -> str:
    if language == "Hebrew":
        return ("\nכל התוכן — כותרות, שאלות, אפשרויות והסברים — חייב להיות "
                "בעברית. מונחים טכניים באנגלית מותרים (למשל TCP, Big O, malloc).\n"
                "IMPORTANT: write ALL content in Hebrew. JSON keys stay in English.")
    return ""


def _build_outline(exam: str, bg: str, scope: str, language: str = "English") -> dict | None:
    return _groq_json([
        {"role": "system", "content": _COMMON + _lang_rule(language)},
        {"role": "user", "content":
         f"EXAM: {exam}\nLEARNER ALREADY KNOWS: {bg}\n"
         + (f"OFFICIAL SYLLABUS (base the whole course on THIS material — cover "
            f"exactly these topics, nothing generic):\n{scope}\n" if scope else "")
         + "Design the course outline as JSON:\n"
         '{"title": "...", "subtitle": "one motivating line", '
         '"units": [{"title": "...", "desc": "one line"}]}\n'
         "4–6 units, ordered from the learner's current level up to exam level, "
         "each unit a coherent theme drawn from the syllabus topics. JSON only."}],
        max_tokens=700)


def _build_unit_levels(exam: str, bg: str, unit: dict, scope: str,
                       language: str = "English") -> list:
    data = _groq_json([
        {"role": "system", "content": _COMMON + _lang_rule(language)},
        {"role": "user", "content":
         f"EXAM: {exam}\nLEARNER KNOWS: {bg}\n"
         f"UNIT: {unit.get('title')} — {unit.get('desc','')}\n"
         + (f"SYLLABUS CONTEXT (draw the questions from this real material):\n"
            f"{scope[:2200]}\n" if scope else "")
         + "Create the levels for THIS unit as JSON:\n"
         '{"levels": [{"title": "...", "teach": "...", "questions": [ ... ]}]}\n'
         "\"teach\" is a SHORT LESSON (markdown, 80–160 words) that TEACHES the "
         "level's concept clearly BEFORE any question — explain the idea, give a "
         "concrete example (use ``` for code/examples), and the key rule to "
         "remember. The questions then test exactly what the lesson taught.\n"
         "Each question is ONE of these three types — USE ALL THREE across the "
         "unit for variety:\n"
         '1) single choice: {"q":"...","type":"mc","options":["a","b","c","d"],"answer":0,"explain":"..."}\n'
         '2) multiple correct: {"q":"Select all that apply: ...","type":"multi","options":["a","b","c","d"],"answer":[0,2],"explain":"..."}\n'
         '3) typed answer: {"q":"...","type":"input","answer":"42","explain":"..."}\n'
         "5–6 levels in this unit, each with a teach lesson and 5–6 questions, "
         "difficulty rising within and across levels. Everything must reflect "
         "THIS unit's real syllabus topics. Every question needs a correct "
         "answer and a one-sentence explanation. JSON only."}],
        max_tokens=4000)
    levels = (data or {}).get("levels") or []
    clean = []
    for lv in levels:
        qs = []
        for q in (lv.get("questions") or []):
            if not q.get("q"):
                continue
            t = q.get("type")
            opts = q.get("options") or []
            if t == "multi" and opts:
                q["type"] = "multi"
                ans = q.get("answer", [])
                if not isinstance(ans, list):
                    ans = [ans]
                q["answer"] = sorted({int(a) for a in ans
                                      if str(a).lstrip("-").isdigit()
                                      and 0 <= int(a) < len(opts)}) or [0]
            elif t == "input" or not opts:
                q["type"] = "input"
                q.setdefault("answer", "")
            else:
                q["type"] = "mc"
                try:
                    q["answer"] = int(q.get("answer", 0))
                except Exception:
                    q["answer"] = 0
                q["answer"] = max(0, min(q["answer"], len(opts) - 1))
            q.setdefault("explain", "")
            qs.append(q)
        if qs:
            clean.append({"title": lv.get("title", "Level"),
                          "teach": (lv.get("teach") or "").strip(),
                          "questions": qs})
    return clean


def _verify_scope(exam: str, base_scope: str) -> str:
    """Research the exam live and merge findings so the material is current and
    correct — even on top of a built-in syllabus."""
    try:
        import research
        md, _ = research.run(f"{exam} — exact topics, syllabus and question "
                             f"types a candidate must know; verify and add any "
                             f"current details.")
        if md and "couldn't" not in md.lower()[:60]:
            return (base_scope + "\n\nVERIFIED / ADDITIONAL FROM RESEARCH:\n"
                    + md[:1800]) if base_scope else md[:2500]
    except Exception as exc:
        log.debug("scope verify failed: %s", exc)
    return base_scope


def build_course(exam: str, background: str = "") -> tuple[str, "Path | None"]:
    """Full interactive-course pipeline (40+ levels, teach-then-test).
    An empty/generic exam builds the full combined prep course from every
    built-in syllabus. Returns (chat_summary, saved_path)."""
    exam = (exam or "").strip()
    bg = background.strip() or "a motivated beginner"

    # ── Study area: no specific exam → combined mega-course of all syllabi ──
    generic = (not exam or exam.lower() in (
        "all", "study", "מודיעין", "intelligence", "army", "צבא", "מבחנים",
        "exams", "everything", "הכל"))
    try:
        import study_syllabus
        segments = []
        if generic:
            for _k, title, lang, _trig, text in study_syllabus.SYLLABI:
                segments.append((title, lang, text))
        else:
            hit = study_syllabus.match(exam)
            if hit:
                segments = [hit]
    except Exception:
        segments = []

    label = exam or "Intelligence Prep — full study area"
    try:
        import activity
        activity.set_group(f"Study course: {label[:38]}")
    except Exception:
        pass

    if segments:
        # Build one large course; each syllabus segment contributes units.
        language = segments[0][1]
        course = {"title": ("מסלול הכנה למודיעין — כל המבחנים"
                            if language == "Hebrew" and generic
                            else (segments[0][0] if not generic else "Intelligence Prep")),
                  "subtitle": ("קורס אינטראקטיבי מלא — מותאם לרקע: " + bg
                               if language == "Hebrew"
                               else "Full interactive course — tailored to: " + bg),
                  "lang": "he" if language == "Hebrew" else "en", "units": []}
        seg_i = 0
        for seg_title, seg_lang, seg_scope in segments:
            seg_i += 1
            aid = _act("search", f"Verifying material: {seg_title[:30]}")
            scope = _verify_scope(seg_title, seg_scope)
            _act_done(aid)
            aid = _act("brain", f"Designing: {seg_title[:30]}")
            outline = _build_outline(seg_title, bg, scope, seg_lang)
            _act_done(aid)
            units = (outline or {}).get("units", [])[:4]
            for u in units:
                aid = _act("note", f"Teaching+quiz: {u.get('title','')[:28]}")
                levels = _build_unit_levels(seg_title, bg, u, scope, seg_lang)
                _act_done(aid, f"{len(levels)} levels")
                if levels:
                    course["units"].append({"title": u.get("title", "Unit"),
                                            "desc": u.get("desc", ""),
                                            "levels": levels})
        outline = {"units": course["units"]}   # for the empty-check below
    else:
        # Unknown exam → research-driven single course.
        language = "English"
        aid = _act("search", f"Researching what the {exam[:34]} covers")
        scope = _verify_scope(exam, _research_scope(exam))
        _act_done(aid, "scope gathered")
        aid = _act("brain", "Designing the course structure")
        outline = _build_outline(exam, bg, scope, language)
        _act_done(aid)
        if not outline or not outline.get("units"):
            return ("I couldn't design the course right now (my models are "
                    "busy) — try again in a minute.", None)
        course = {"title": outline.get("title") or f"{exam} — Study Course",
                  "subtitle": outline.get("subtitle") or f"Tailored for: {bg}",
                  "lang": "en", "units": []}
        for i, u in enumerate(outline["units"][:8], 1):
            aid = _act("note", f"Teaching+quiz {i}: {u.get('title','')[:26]}")
            levels = _build_unit_levels(exam, bg, u, scope, language)
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
        # Use a function replacement — a plain string replacement would let
        # re.sub interpret the JSON's \n / \t escapes as real control chars,
        # corrupting the injected JavaScript.
        _repl = ("/*__COURSE__*/ " + json.dumps(course, ensure_ascii=False)
                 + " /*__END__*/")
        injected = re.sub(r"/\*__COURSE__\*/[\s\S]*?/\*__END__\*/",
                          lambda _m: _repl, template, count=1)
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", (exam or "study_area").lower()).strip("_")[:40] or "course"
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
               f"your browser — a Duolingo-style study area with **{n_levels} "
               f"levels** and **{n_q} questions**, tailored to: {bg}.\n\n"
               f"Each level **teaches the material first**, then tests you on it "
               f"with a mix of question types (choose one, choose many, and typed "
               f"answers). Instant feedback, XP, streaks, and progress save "
               f"automatically.\n\n**Units:**\n{unit_list}\n\n"
               f"Ask me to add harder levels or a new topic anytime.")
    return summary, saved


# Back-compat: the study_prep action calls prepare(); keep it pointing here.
def prepare(exam: str, background: str = "") -> tuple[str, "Path | None"]:
    return build_course(exam, background)
