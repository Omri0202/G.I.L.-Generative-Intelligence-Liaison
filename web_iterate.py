"""
web_iterate.py — G.I.L. conversational website editing + publishing.

edit(instruction)  — "make the hero darker, rename Services to Menu":
    loads the most recently generated site, applies a targeted LLM rewrite,
    re-injects the customizer, saves and reopens it.

publish() — pushes the latest site to a public GitHub Pages URL using the
    already-authenticated gh CLI.
"""

import os
import re
import subprocess
import time
from pathlib import Path

from logger import get as _get_log

log = _get_log("webiter")

_OUT_DIR = Path.home() / "Documents" / "GIL_Websites"


def find_latest_site() -> Path | None:
    """Newest generated site folder containing an index.html."""
    candidates = []
    if _OUT_DIR.is_dir():
        for d in _OUT_DIR.iterdir():
            if d.is_dir() and (d / "index.html").exists():
                candidates.append((d / "index.html").stat().st_mtime and d)
    if not candidates:
        return None
    return max(candidates, key=lambda d: (d / "index.html").stat().st_mtime)


def _strip_editor(html: str) -> str:
    """Remove the injected customizer block so the LLM sees clean HTML."""
    return re.sub(r"<!-- G\.I\.L\. Customizer -->[\s\S]*?</script>\s*(?=</body|$)",
                  "", html, flags=re.IGNORECASE)


def edit(instruction: str) -> tuple[str, Path | None]:
    """Apply a change instruction to the latest generated site."""
    folder = find_latest_site()
    if not folder:
        return ("I don't have a generated website to edit yet — ask me to "
                "build one first.", None)
    index = folder / "index.html"
    html = _strip_editor(index.read_text(encoding="utf-8", errors="replace"))
    if len(html) > 60_000:
        html = html[:60_000]

    try:
        import activity
        aid = activity.start("web", f"Editing site: {instruction[:50]}")
    except Exception:
        aid = None

    prompt = (
        "You are editing an EXISTING website. Apply ONLY this change, keep "
        "everything else byte-identical where possible:\n"
        f"CHANGE REQUEST: {instruction}\n\n"
        "Return the COMPLETE updated HTML document, starting with <!DOCTYPE html>. "
        "No markdown fences, no commentary.\n\nCURRENT HTML:\n" + html
    )
    from webgen import _generate_claude, _generate_groq, _anthropic_key, _groq_keys, _fix_html
    key = _anthropic_key()
    out = _generate_claude(prompt, key) if key else _generate_groq(prompt, _groq_keys())
    if out.startswith("ERROR:"):
        if aid is not None:
            try:
                import activity
                activity.fail(aid, out[:80])
            except Exception:
                pass
        return ("I couldn't apply the edit — my AI models are busy. "
                "Try again in a minute.", None)
    out = re.sub(r"^```[^\n]*\n?", "", out)
    out = re.sub(r"\n?```\s*$", "", out).strip()
    if not out.lower().startswith("<!doctype"):
        return ("The edit came back malformed, so I left the site untouched. "
                "Try rephrasing the change.", None)
    out = _fix_html(out)
    try:
        from web_editor import inject_editor
        out = inject_editor(out)
    except Exception:
        pass
    # keep a backup of the previous version
    try:
        (folder / f"index.backup-{int(time.time())}.html").write_text(
            index.read_text(encoding="utf-8", errors="replace"), encoding="utf-8")
    except Exception:
        pass
    index.write_text(out, encoding="utf-8")
    if aid is not None:
        try:
            import activity
            activity.done(aid)
        except Exception:
            pass
    try:
        os.startfile(str(index))
    except Exception:
        pass
    return (f"Updated — '{folder.name.replace('_', ' ')}' reloaded with the change.",
            index)


def publish() -> str:
    """Publish the latest site to GitHub Pages via the gh CLI."""
    folder = find_latest_site()
    if not folder:
        return "There's no generated website to publish yet."
    slug = re.sub(r"[^a-z0-9-]", "-", folder.name.lower())[:40].strip("-") or "gil-site"
    repo = f"gil-site-{slug}"

    try:
        import activity
        aid = activity.start("web", f"Publishing {slug} to the web")
    except Exception:
        aid = None

    def _run(args, cwd=None):
        return subprocess.run(args, cwd=cwd or str(folder), capture_output=True,
                              text=True, timeout=180,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    try:
        who = _run(["gh", "api", "user", "--jq", ".login"])
        owner = who.stdout.strip()
        if not owner:
            raise RuntimeError("gh CLI is not authenticated")
        if not (folder / ".git").exists():
            _run(["git", "init", "-b", "main"])
        _run(["git", "add", "-A"])
        _run(["git", "commit", "-m", "publish via G.I.L."])
        create = _run(["gh", "repo", "create", repo, "--public",
                       "--source", ".", "--push"])
        if create.returncode != 0 and "already exists" in (create.stderr or ""):
            _run(["git", "push", f"https://github.com/{owner}/{repo}.git",
                  "main", "--force"])
        elif create.returncode != 0:
            raise RuntimeError((create.stderr or "repo create failed")[:120])
        pages = _run(["gh", "api", f"repos/{owner}/{repo}/pages", "-X", "POST",
                      "-f", "source[branch]=main", "-f", "source[path]=/"])
        # 409 = pages already enabled — fine
        url = f"https://{owner}.github.io/{repo}/"
        if aid is not None:
            import activity
            activity.done(aid, url)
        return (f"Published! Your site is live at {url} — "
                "it can take a minute to come online the first time.")
    except Exception as exc:
        log.error("publish failed: %s", exc)
        if aid is not None:
            try:
                import activity
                activity.fail(aid, str(exc)[:80])
            except Exception:
                pass
        return f"Publishing failed — {str(exc)[:100]}"
