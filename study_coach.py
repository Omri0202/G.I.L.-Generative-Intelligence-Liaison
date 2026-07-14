"""
study_coach.py — G.I.L. exam preparation & learning coach.

Given an exam (and what the user already knows), GIL:
  1. RESEARCHES what the exam actually covers (live, via research.py).
  2. Builds a detailed, high-level STUDY GUIDE: topic breakdown, key concepts
     to master, and a study plan tailored to the user's background.
  3. Generates a real PRACTICE TEST (questions + worked answers).
  4. Generates hands-on LEARNING EXERCISES.
The full guide is saved as markdown (Documents/GIL_Study) and opened, with a
summary posted in the chat.

Used by the /study command and the study_prep brain action; also callable
inside the autonomous loop for a full "prepare me for X" goal.
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

from logger import get as _get_log

log = _get_log("study")

_OUT_DIR = Path.home() / "Documents" / "GIL_Study"


def _groq(messages: list, max_tokens: int = 2600) -> str | None:
    import requests
    keys = [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]
    for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
        for key in keys:
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": model, "messages": messages,
                          "temperature": 0.4, "max_tokens": max_tokens},
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    timeout=45)
                if r.status_code == 429:
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
            except Exception as exc:
                log.debug("study groq attempt failed: %s", exc)
    return None


def _research_scope(exam: str) -> str:
    """Find out what the exam covers (grounded in live web results)."""
    try:
        import research
        chat_md, _ = research.run(
            f"What topics, subjects and skills are tested in the {exam}? "
            f"What should a candidate study to prepare?")
        return chat_md
    except Exception as exc:
        log.warning("scope research failed: %s", exc)
        return ""


def _section(aid, kind, label):
    try:
        import activity
        return activity.start(kind, label)
    except Exception:
        return None


def _done(aid, detail=""):
    try:
        import activity
        if aid is not None:
            activity.done(aid, detail[:100])
    except Exception:
        pass


def prepare(exam: str, background: str = "") -> tuple[str, "Path | None"]:
    """Full prep pipeline. Returns (chat_summary_markdown, saved_path)."""
    exam = (exam or "").strip()
    if not exam:
        return ("Which exam should I prepare you for? Tell me its name and "
                "what you already know.", None)

    grp = None
    try:
        import activity
        activity.set_group(f"Exam prep: {exam[:40]}")
    except Exception:
        pass

    # 1. Research the exam scope
    aid = _section(None, "search", f"Researching what the {exam[:40]} covers")
    scope = _research_scope(exam)
    _done(aid, "scope gathered" if scope else "used general knowledge")

    bg = background.strip() or "not specified"
    common = (
        "You are G.I.L., an expert tutor and exam coach. Be detailed, "
        "high-level and concrete — this is serious preparation, not a summary.")

    # 2. Study guide + plan
    aid = _section(None, "note", "Writing the study guide")
    guide = _groq([
        {"role": "system", "content": common},
        {"role": "user", "content":
         f"EXAM: {exam}\nWHAT I ALREADY KNOW: {bg}\n\n"
         + (f"RESEARCHED SCOPE (ground your plan in this):\n{scope[:4000]}\n\n"
            if scope else "")
         + "Write a thorough markdown STUDY GUIDE with:\n"
         "## What this exam tests — the real topics and skills.\n"
         "## What you must master — each topic with the specific concepts, "
         "broken down, and WHY it matters for this exam.\n"
         "## Study plan — a realistic ordered plan that starts from my current "
         "background and fills the gaps to reach exam level.\n"
         "Be specific with concept names, not vague. Assume I'm smart but new "
         "to most of this beyond what I said I know."}],
        max_tokens=2600) or "Couldn't generate the study guide right now."
    _done(aid)

    # 3. Practice test
    aid = _section(None, "code", "Building a practice test")
    test = _groq([
        {"role": "system", "content": common},
        {"role": "user", "content":
         f"EXAM: {exam}\nMY BACKGROUND: {bg}\n\n"
         + (f"SCOPE:\n{scope[:2500]}\n\n" if scope else "")
         + "Create a DETAILED PRACTICE TEST in markdown that mirrors the real "
         "exam's style and difficulty. Include a mix: multiple-choice, "
         "short-answer, logic/pattern problems, and at least 2 coding/"
         "algorithm problems. 12–18 questions, escalating difficulty. "
         "Number them. Then a '## Answer key' section with worked solutions "
         "and short explanations."}],
        max_tokens=3000) or "Couldn't generate the practice test right now."
    _done(aid)

    # 4. Learning exercises
    aid = _section(None, "code", "Creating learning exercises")
    exercises = _groq([
        {"role": "system", "content": common},
        {"role": "user", "content":
         f"EXAM: {exam}\nMY BACKGROUND: {bg}\n\n"
         "Create a set of hands-on LEARNING EXERCISES (markdown) that build "
         "the skills this exam needs, from my current level upward. For each: "
         "a title, what it trains, the task, and a hint. Include practical "
         "drills (e.g. code to write, problems to solve, patterns to practice) "
         "— not just reading. 8–12 exercises, ordered easy→hard."}],
        max_tokens=2400) or "Couldn't generate exercises right now."
    _done(aid)

    try:
        import activity
        activity.clear_group()
    except Exception:
        pass

    # Assemble + save
    today = datetime.now().strftime("%Y-%m-%d")
    doc = (f"# Exam Prep — {exam}\n_Prepared by G.I.L. on {today}. "
           f"Your stated background: {bg}._\n\n"
           f"{guide}\n\n---\n\n# Practice Test\n\n{test}\n\n---\n\n"
           f"# Learning Exercises\n\n{exercises}\n")

    saved = None
    try:
        _OUT_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "_", exam.lower()).strip("_")[:50] or "exam"
        saved = _OUT_DIR / f"{slug}_{today}.md"
        saved.write_text(doc, encoding="utf-8")
        try:
            os.startfile(str(saved))   # open the guide for the user
        except Exception:
            pass
    except Exception as exc:
        log.warning("could not save study guide: %s", exc)

    # Chat summary = the guide + a pointer to the full file
    summary = (f"I've prepared a full study pack for **{exam}** — guide, "
               f"practice test, and exercises"
               + (f" (saved to `{saved}` and opened for you)." if saved else ".")
               + "\n\n" + guide[:1800]
               + ("\n\n*…full practice test and exercises are in the saved "
                  "guide.*" if saved else ""))
    return summary, saved
