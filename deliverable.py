"""deliverable.py — the shared Content Deliverable Manifesto.

Single source of truth for GIL's document/essay/report/presentation/code-review
standards, imported by BOTH the code agent (code_agent.py, the /codemode terminal)
and the chat brain (gil_brain.py, the web chat). One constant => zero prompt drift
between the two surfaces.
"""

# Words that mean "write me prose / a document / a deck" (English + Hebrew), i.e.
# a task that needs no file inspection and should go straight to a rich deliverable.
CONTENT_HINTS = (
    "essay", "treatise", "presentation", "slide deck", "slides", "cover letter",
    "research paper", "article", "compose", "write an ", "write a report",
    "write me a", "write a letter", "write a document", "study guide",
    # Hebrew: essay/assignment, presentation, document, composition, article,
    # report, write (2 forms), letter
    "עבודה", "מצגת", "מסמך", "חיבור", "מאמר", "כתבה", "דוח", "כתוב", "תכתוב", "מכתב",
)


def is_content_task(task: str) -> bool:
    """True when the task is to WRITE prose / a document / a deck (no file work),
    so the surface can skip its tool/command machinery and give the deliverable
    room to breathe."""
    t = (task or "").lower()
    return any(h in t for h in CONTENT_HINTS)


# Language discipline — reused verbatim so Hebrew/English behave identically.
LANGUAGE_RULE = (
    "Reply in the user's language. A Hebrew request -> flawless, high-register "
    "Hebrew (correct grammar, academic register, right-to-left); keep code, "
    "filenames and identifiers in `monospace` so they never break text direction. "
    "Mixed input -> match the dominant language. JSON keys always stay English."
)

# The manifesto itself — output-shape agnostic (each surface says WHERE the text
# goes: the code agent puts it in `done.summary`, the chat brain in `report`).
MANIFESTO = """DELIVERABLE STANDARDS — when creating prose content (essay, report,
document, presentation script, cover letter, study material, code review):
- NO banter: never "Sure" / "Here is your essay". Open with the document's title or
  first real line. Institutional quality — ready to hand a professor or lead
  engineer AS-IS, from index zero of the output.
- STRUCTURE: a clear title, hierarchical `##` headings, rich paragraphs with dense
  domain vocabulary, and a Markdown table for any comparison/metrics. For a
  PRESENTATION, output one block per slide, each with **Visual directive**,
  **On-screen bullets**, and a 3-4 sentence **Speaker script** (no empty bullets).
- LENGTH LOCK: if a minimum is given ("at least 300 words"), OVER-shoot it by
  30-50% with real, substantive content — never padding, repetition, or
  "...continue here" placeholders. Expand every section to full depth.
- CODE REVIEW ("review/fix this code"): give (1) a line-by-line defect matrix, (2)
  the COMPLETE corrected code (no elision, no "keep original here"), (3) a Markdown
  metrics table (time/space complexity, error handling — before -> after).
- Run the 4-stage discipline in your hidden reasoning FIRST: DECONSTRUCT the
  constraints (type, length, language, domain) -> draft the full section SCHEMA ->
  EXPAND each section to full depth -> AUDIT depth, length, language and formatting,
  looping to expand any thin section BEFORE you emit the final text."""
