"""
diff_hunks.py — hunk-by-hunk diffing for G.I.L.'s interactive diff reviewer
(Feature #3).

Splits a proposed file change into individually-approvable hunks and reconstructs
the final file from a per-hunk accept / reject / edit decision. Pure standard
library (difflib), no GUI or network — so the logic is fully unit-testable.

A *hunk* is a maximal run of changed lines (with a little surrounding context for
display). Everything outside a hunk is unchanged and is always kept verbatim, so
the accepted subset always splices cleanly back into the original file.
"""

from __future__ import annotations

import difflib


def _split(text: str) -> list[str]:
    """Split into lines while remembering whether the text ended with a newline,
    so reconstruction is byte-faithful."""
    return text.splitlines(keepends=True)


def _runs(orig: list[str], prop: list[str]):
    """Yield (is_change, i1, i2, j1, j2) segments over the two line lists, merging
    adjacent changed opcodes into a single run. Equal segments come through as
    is_change=False; changed runs (replace/insert/delete) as is_change=True."""
    sm = difflib.SequenceMatcher(a=orig, b=prop, autojunk=False)
    pending = None   # accumulate consecutive non-equal opcodes into one run
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            if pending:
                yield (True, *pending)
                pending = None
            yield (False, i1, i2, j1, j2)
        else:
            if pending:
                pending = (pending[0], i2, pending[2], j2)   # extend i2/j2
            else:
                pending = (i1, i2, j1, j2)
    if pending:
        yield (True, *pending)


def build_hunks(original: str, proposed: str, context: int = 3) -> list[dict]:
    """Return the list of changed hunks between two file contents. Each hunk:
        id           - stable 1-based index
        old_start    - 1-based first changed line in the original (0 for pure insert)
        old_count    - changed original line count
        new_count    - changed proposed line count
        old_text     - the original slice (for the 'reject' side / editing)
        new_text     - the proposed slice (the default 'accept' side)
        header       - unified-diff style "@@ -a,b +c,d @@"
        lines        - display rows: {tag: ' '|'-'|'+', text: str}
    """
    orig, prop = _split(original), _split(proposed)
    segs = list(_runs(orig, prop))
    hunks: list[dict] = []
    hid = 0
    for idx, (is_change, i1, i2, j1, j2) in enumerate(segs):
        if not is_change:
            continue
        hid += 1
        # a few equal lines on each side, purely for readable context
        before = segs[idx - 1] if idx > 0 and not segs[idx - 1][0] else None
        after = segs[idx + 1] if idx + 1 < len(segs) and not segs[idx + 1][0] else None
        rows: list[dict] = []
        if before:
            for ln in orig[max(before[1], before[2] - context):before[2]]:
                rows.append({"tag": " ", "text": ln})
        for ln in orig[i1:i2]:
            rows.append({"tag": "-", "text": ln})
        for ln in prop[j1:j2]:
            rows.append({"tag": "+", "text": ln})
        if after:
            for ln in orig[after[1]:min(after[2], after[1] + context)]:
                rows.append({"tag": " ", "text": ln})
        hunks.append({
            "id": hid,
            "old_start": i1 + 1 if i2 > i1 else 0,
            "old_count": i2 - i1,
            "new_start": j1 + 1 if j2 > j1 else 0,
            "new_count": j2 - j1,
            "old_text": "".join(orig[i1:i2]),
            "new_text": "".join(prop[j1:j2]),
            "header": f"@@ -{i1 + 1},{i2 - i1} +{j1 + 1},{j2 - j1} @@",
            "lines": rows,
        })
    return hunks


def apply_hunks(original: str, proposed: str, decisions: dict, context: int = 3) -> str:
    """Rebuild the file from a per-hunk decision map.

    decisions: {hunk_id: {"action": "accept"|"reject"|"edit", "text": <edit only>}}
    Missing / unknown ids default to ACCEPT (the diff was shown; silence = take it).
    A rejected hunk keeps the original lines; an edited hunk uses the supplied text
    verbatim (its own trailing newline is normalized to match the slice it replaces).
    """
    orig, prop = _split(original), _split(proposed)
    out: list[str] = []
    hid = 0
    for is_change, i1, i2, j1, j2 in _runs(orig, prop):
        if not is_change:
            out.extend(orig[i1:i2])
            continue
        hid += 1
        dec = decisions.get(hid) or decisions.get(str(hid)) or {}
        action = (dec.get("action") or "accept").lower()
        if action == "reject":
            out.extend(orig[i1:i2])
        elif action == "edit":
            text = dec.get("text", "")
            # keep the replaced slice's newline convention so nothing runs together
            replaced_has_nl = (prop[j1:j2] or orig[i1:i2] or [""])[-1].endswith("\n")
            if text and not text.endswith("\n") and replaced_has_nl:
                text += "\n"
            out.append(text)
        else:  # accept (default)
            out.extend(prop[j1:j2])
    return "".join(out)


def summarize(hunks: list[dict]) -> str:
    """One-line human summary of the pending change, e.g. '3 hunks · +12 −4'."""
    added = sum(h["new_count"] for h in hunks)
    removed = sum(h["old_count"] for h in hunks)
    n = len(hunks)
    return f"{n} hunk{'s' if n != 1 else ''} · +{added} −{removed}"
