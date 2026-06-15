"""
webgen.py — Project G.I.L.
Website generation: reads TASK.md from project folder (or uses voice description),
calls LLM with a highly prescriptive prompt, saves index.html, opens in browser.
"""

import json
import os
import re
import webbrowser
from datetime import datetime
from pathlib import Path

_OUT_DIR       = Path.home() / "Documents" / "GIL_Websites"
_CLAUDE_MODEL  = "claude-sonnet-4-6"   # same model powering Claude Code

# ── System prompt ─────────────────────────────────────────────────────────────
# Extremely prescriptive — tells the LLM exactly what HTML/CSS/JS patterns to use.

_SYSTEM = """\
You are a world-class creative frontend engineer and UI/UX designer. \
Generate a COMPLETE, STUNNING, single-file HTML website perfectly tailored to the subject. \
Output ONLY raw HTML — start with <!DOCTYPE html>. No markdown, no fences, no explanation.

══ NON-NEGOTIABLE RULES ══
• Single file: <style> in <head>, <script> before </body>.
• External resources allowed: Google Fonts @import, Font Awesome CDN, Unsplash image URLs.
• Real, specific copy everywhere — invent names, stories, details. NEVER Lorem Ipsum.
• Fully responsive: CSS Grid + Flexbox, breakpoints at 768px and 480px.
• DO NOT force a commercial/pricing structure. Design for the actual subject.

══ PHOTOGRAPHY — USE LOREMFLICKR FOR ALL IMAGES ══
Every image must use loremflickr.com — it returns real, on-topic Flickr photos filtered by keyword.
Format: https://loremflickr.com/WIDTH/HEIGHT/KEYWORD1,KEYWORD2?lock=N
• WIDTH and HEIGHT are pixel dimensions (integers, no slash between them like picsum — use /WIDTH/HEIGHT/).
• KEYWORD1,KEYWORD2 — pick 1-3 SPECIFIC words that match the exact subject. For tennis: "tennis,sport". For drumming: "drums,percussion". For coffee shop: "coffee,cafe". For hiking: "hiking,nature,trail".
• lock=N — an integer (1, 2, 3, ...) that locks to a specific photo. Use a DIFFERENT N for each image on the page so every image is unique. N can be 1–99.
• Standard sizes:
    Hero background (CSS): 1920/1080
    Wide section image: 1200/800
    Card / grid item: 800/600
    Portrait: 600/800
    Square thumbnail: 600/600
• For CSS background: background-image: url('https://loremflickr.com/1920/1080/KEYWORDS?lock=1');
• For <img> tags: <img src="https://loremflickr.com/800/600/KEYWORDS?lock=2" ...>
• ALWAYS add loading="lazy" to non-hero images.
• Aim for 8–14 images per page. Gallery grids: at least 6 cards, each with its own lock number.
• NEVER use picsum.photos, placeholder.com, via.placeholder.com, or source.unsplash.com.

══ FONT AWESOME ICONS ══
Include in <head>:
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
Use for nav icons, social links, feature icons, bullet points, stats — everywhere an icon improves clarity.

══ ADAPTIVE SECTION STRUCTURE ══
Choose sections that MAKE SENSE for this specific subject. DO NOT default to a generic commercial template.

Music / Band:
  Hero (with live concert photo) → Story / Origins → Discography / Albums (with cover art photos) → Tour Dates → Gallery (masonry grid) → Members → Listen / Spotify embed placeholder → Footer

Art / Photography / Creative portfolio:
  Full-screen hero image → Gallery grid (masonry, 3–4 col, hover zoom) → Artist statement → Selected works (large feature images) → Process / Behind the scenes → Exhibitions / Shows → Contact → Footer

Nature / Travel / Lifestyle:
  Cinematic hero → Destinations or Topics (card grid with photos) → Featured story (image + text split) → Gallery → Tips / Guide → Community / Social proof → Newsletter → Footer

Restaurant / Cafe / Food:
  Atmospheric hero → Story → Menu by category (tabs, food photos) → Photo gallery → Chef / Team → Location + hours → Reservations → Footer

Personal / Blog / Writer:
  Bold typographic hero → About (photo + bio) → Featured posts (large cards with images) → Topics / Categories → Reading list → Newsletter → Contact → Footer

Tech / Product / App:
  Hero with device mockup or screenshot → Problem statement → Features (icon + text cards) → How it works (numbered steps) → Screenshots gallery → Testimonials → Pricing (ONLY if relevant) → CTA → Footer

Event / Festival / Conference:
  Countdown hero → Lineup (speaker/artist cards with photos) → Schedule → Venue (photo + map) → Highlights gallery from past events → Sponsors → Tickets → Footer

Nonprofit / Community / Cause:
  Emotional hero photo → Mission → Impact stats → Stories (photo + quote cards) → Team → How to help → Donate / Join → Footer

Fitness / Sports / Wellness:
  Dynamic action hero → Programs (photo cards) → Results / Transformations gallery → Schedule → Trainers (portrait photos) → Testimonials → Join CTA → Footer

REQUIRED in every site: nav, hero, footer. Everything else: choose what fits.

══ VISUAL IDENTITY — match mood to subject ══
Each site needs a UNIQUE color palette and typography that fits its subject:
• Music / Night life → deep dark bg (#0a0a0f), electric accent (neon purple, cyan, or magenta)
• Nature / Wellness → warm off-white or forest green bg, earthy accent (terracotta, sage)
• Art / Creative → bold contrast, possibly light bg, vivid accent (orange, crimson, violet)
• Food / Hospitality → warm amber tones, rich reds or greens, appetite-inducing warmth
• Tech / SaaS → clean dark or very light, blue/violet gradient accent
• Luxury / Fashion → near-black or cream, gold or silver accent, lots of whitespace
• Sports / Energy → dark bg, high-contrast accent (electric yellow, red, neon orange)
• Personal / Blog → clean light bg, soft accent, excellent typography

Typography rule: ALWAYS pick a Google Fonts pairing — one display font for headings, one clean sans-serif for body. E.g. "Playfair Display" + "Inter", "Space Grotesk" + "Lato", "DM Serif Display" + "DM Sans".

══ CSS ARCHITECTURE ══
:root {
  --bg, --bg2, --bg3   /* 3 background depth tones */
  --card               /* card surface */
  --border             /* subtle border */
  --accent             /* primary brand color */
  --accent2            /* lighter/darker accent variant */
  --text               /* main text */
  --muted              /* secondary text */
  --radius: 14px
}
html { scroll-behavior: smooth; }
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

══ HERO — make it cinematic ══
• min-height: 100vh; position: relative; overflow: hidden;
• ALWAYS use a full-bleed loremflickr background:
  background: linear-gradient(to bottom, rgba(0,0,0,0.55) 0%, rgba(0,0,0,0.2) 50%, rgba(0,0,0,0.7) 100%),
              url('https://loremflickr.com/1920/1080/SUBJECT_KEYWORDS?lock=1') center/cover no-repeat fixed;
• OR for a split layout: left side = text content; right side = large Unsplash <img>
• OR for art/portfolio: full-screen image grid as background with mix-blend-mode overlay
• Headline: clamp(2.8rem, 7vw, 6rem), font-weight: 800 or 900
  — wrap one key word in <span class="accent-word"> with color: var(--accent)
• Subheadline: max-width: 560px, var(--muted), font-size: clamp(1rem, 2.5vw, 1.25rem)
• CTA buttons (if applicable): .btn-primary solid, .btn-ghost transparent bordered
• Entrance animation: @keyframes fadeUp { from { opacity:0; transform:translateY(28px) } to { opacity:1; transform:none } }
  Apply with staggered animation-delay (0.1s, 0.25s, 0.4s, 0.6s) on each child element

══ IMAGE CARDS ══
• Always use <img> with object-fit: cover and a fixed aspect ratio container (aspect-ratio: 16/9 or 4/3 or 1/1)
• Hover: transform: scale(1.04); transition: 0.4s ease; with overflow: hidden on parent
• On hover of parent card: border-color changes to accent, subtle box-shadow deepens
• Scroll-reveal: initial opacity:0; transform:translateY(24px); transition:0.5s ease → .visible: opacity:1; transform:none

══ GALLERY SECTIONS ══
• Use CSS Grid: grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px;
• Each grid item: position:relative; overflow:hidden; border-radius: var(--radius);
  aspect-ratio: 4/3 (or 1/1 for square); cursor: pointer;
• <img>: width:100%; height:100%; object-fit:cover; transition: transform 0.5s ease;
• Hover: img scales to 1.08, overlay appears with title text
• For masonry feel: use grid-row: span 2 on selected items

══ NAVBAR ══
• position: fixed; top:0; left:0; right:0; z-index:1000; height:64px;
• backdrop-filter: blur(16px) saturate(180%); background: rgba(bg-color, 0.8);
• border-bottom: 1px solid var(--border);
• Logo (text or SVG icon) + nav links + icon buttons (Font Awesome) + optional CTA
• JS: add class "scrolled" after 80px scroll → border-bottom becomes accent color at 30%

══ SCROLL-REVEAL ══
const observer = new IntersectionObserver(entries => {
  entries.forEach((e, i) => {
    if (e.isIntersecting) {
      setTimeout(() => e.target.classList.add('visible'),
        [...e.target.parentElement.children].indexOf(e.target) * 80);
      observer.unobserve(e.target);
    }
  });
}, { threshold: 0.1 });
document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
Add class "reveal" to every card, section image, and content block.

══ FOOTER ══
• Background: var(--bg) or slightly darker; border-top: 1px solid var(--border);
• Padding: 60px 5vw 32px;
• Grid layout: brand column (logo + tagline + social icons) | links columns | optional newsletter
• Social icons: Font Awesome brands (fa-instagram, fa-spotify, fa-youtube, fa-twitter, etc.) — pick ones relevant to the subject
• Copyright line centered below

OUTPUT: Only the HTML. Start with <!DOCTYPE html>.\
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _anthropic_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "")


def _groq_keys() -> list[str]:
    return [k for k in [os.getenv("GROQ_API_KEY", ""), os.getenv("GROQ_API_KEY_2", "")] if k]


def _sanitize(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "_", slug).strip("_")
    return slug[:40] or "website"


def _open_file(path: Path) -> None:
    # ShellExecuteW is the most reliable way to open a file on Windows from any
    # thread — no subprocess, no black window, uses the default browser directly.
    import ctypes
    ret = ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), None, None, 1)
    if ret <= 32:   # <= 32 means error (SE_ERR_*)
        print(f"[G.I.L. WEBGEN] ShellExecute failed ({ret}), trying webbrowser.")
        webbrowser.open(path.as_uri())


def _generate_html(description: str) -> str:
    """
    Core generation. Tries Claude (Anthropic) first — same model as Claude Code,
    so output quality matches what Claude Code writes directly.
    Falls back to Groq if Anthropic key is missing.
    Returns raw HTML string, or a string starting with 'ERROR:'.
    """
    print(f"[G.I.L. WEBGEN] Description: {description[:120]}")

    ant_key = _anthropic_key()
    if ant_key:
        html = _generate_claude(description, ant_key)
    else:
        keys = _groq_keys()
        if not keys:
            return "ERROR: No API key found — add GROQ_API_KEY to your .env file."
        html = _generate_groq(description, keys)

    if html.startswith("ERROR:"):
        return html

    # Strip any accidental markdown fences
    html = re.sub(r"^```[^\n]*\n?", "", html)
    html = re.sub(r"\n?```\s*$", "", html).strip()

    if not html.lower().startswith("<!doctype"):
        print(f"[G.I.L. WEBGEN] Bad output: {html[:120]}")
        return "ERROR: AI returned unexpected content. Try rephrasing your request."

    return html


def _generate_claude(description: str, api_key: str) -> str:
    """Use Anthropic Claude — same model powering Claude Code, highest quality output."""
    try:
        import anthropic
        print(f"[G.I.L. WEBGEN] Using Claude ({_CLAUDE_MODEL})...")
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_CLAUDE_MODEL,
            max_tokens=8096,
            system=_SYSTEM,
            messages=[{"role": "user", "content": f"Build a beautiful, unique website for: {description}\n\nUse loremflickr.com for ALL images — format: https://loremflickr.com/WIDTH/HEIGHT/KEYWORDS?lock=N where KEYWORDS match '{description}' specifically. Different lock number for each image. Color palette and sections tailored to this exact subject — NOT a generic commercial template."}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"[G.I.L. WEBGEN] Claude error: {exc} — falling back to Groq.")
        keys = _groq_keys()
        if keys:
            return _generate_groq(description, keys)
        return f"ERROR: Claude failed and no Groq fallback — {exc.__class__.__name__}."


def _generate_groq(description: str, api_keys: list[str]) -> str:
    """Groq fallback — rotates both keys, retries once on 429."""
    import requests as _rq
    import time as _time
    _GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    print("[G.I.L. WEBGEN] Using Groq fallback...")
    payload = {
        "model": "llama-3.3-70b-versatile",
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user",   "content": f"Build a beautiful, unique website for: {description}\n\nUse loremflickr.com for ALL images — format: https://loremflickr.com/WIDTH/HEIGHT/KEYWORDS?lock=N where KEYWORDS match '{description}' specifically (e.g. for tennis use 'tennis,sport', for drums use 'drums,percussion'). Use a different lock number (1,2,3...) for every image so they are all unique. Color palette, layout, and sections must be tailored to this exact subject — NOT a generic commercial template."},
        ],
        "max_tokens": 8192,
        "temperature": 0.75,
    }
    for attempt, key in enumerate(api_keys * 2):   # try each key twice
        hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            resp = _rq.post(_GROQ_URL, json=payload, headers=hdrs, timeout=120)
            if resp.status_code == 429:
                print(f"[G.I.L. WEBGEN] Groq rate limited on key {attempt % len(api_keys) + 1}, retrying...")
                _time.sleep(3)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except _rq.exceptions.Timeout:
            print("[G.I.L. WEBGEN] Groq timed out, retrying...")
            continue
        except Exception as exc:
            print(f"[G.I.L. WEBGEN] Groq key {attempt % len(api_keys) + 1} error: {exc}")
            continue
    return "ERROR: Groq failed on all keys — check your GROQ_API_KEY in .env."


_GENERIC_WORDS = {
    "website", "webpage", "page", "site", "web", "app", "application",
    "build", "create", "make", "generate", "design", "write",
    "your", "mine", "just", "please", "could", "would", "want",
    "the", "for", "and", "with", "that", "this", "some", "need",
}


def _extract_description(utterance: str) -> str:
    """
    Pull the actual subject out of a voice command.
    'build me a website for a coffee shop'  ->  'coffee shop'
    'create a landing page for my fitness app'  ->  'fitness app'
    'make a portfolio website'  ->  'portfolio'
    """
    t = utterance.strip()
    # Try: grab everything after "for [a/my]"
    m = re.search(r"\bfor\s+(?:my\s+|a\s+|an\s+|the\s+)?(.+)$", t, re.IGNORECASE)
    if m:
        subject = m.group(1).strip()
        # Drop trailing "website/page/app" if it leaked in
        subject = re.sub(
            r"\s*(website|webpage|web\s+page|landing\s+page|web\s+app|site|page)$",
            "", subject, flags=re.IGNORECASE,
        ).strip()
        if len(subject) > 2:
            return subject

    # Fallback: strip the action + "website" wrapper from the front
    cleaned = re.sub(
        r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
        r"(?:build|create|make|generate|design|write)\s+"
        r"(?:me\s+)?(?:a\s+|an\s+)?"
        r"(?:website|webpage|web\s+page|landing\s+page|web\s+app|site)?\s*"
        r"(?:(?:for|about)\s+(?:me\s+)?(?:my\s+|a\s+|an\s+)?)?",
        "", t, flags=re.IGNORECASE,
    ).strip()
    # Also strip a leading "about" that slipped through
    cleaned = re.sub(r"^about\s+", "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned if len(cleaned) > 2 else ""


def _find_web_project(text: str) -> Path | None:
    """
    Search Desktop and Documents for a project folder with a TASK.md whose
    name contains a NON-GENERIC word that also appears in the voice command.
    Generic words like 'website', 'build', 'page' are excluded so a folder
    literally named 'website' doesn't match every command.
    """
    text_lower = text.lower()
    search_dirs = [Path.home() / "Desktop", Path.home() / "Documents"]

    candidates: list[tuple[Path, bool, float]] = []
    for base in search_dirs:
        if not base.exists():
            continue
        try:
            for folder in base.iterdir():
                if not folder.is_dir():
                    continue
                task = next(
                    (folder / n for n in ("TASK.md", "task.md", "Task.md")
                     if (folder / n).exists()),
                    None,
                )
                if task is None:
                    continue
                fname_words = re.split(r"[-_\s]+", folder.name.lower())
                # Only count a match if the word is specific (not a generic action word)
                mentioned = any(
                    w in text_lower
                    for w in fname_words
                    if len(w) > 3 and w not in _GENERIC_WORDS
                )
                candidates.append((folder, mentioned, task.stat().st_mtime))
        except Exception:
            continue

    if not candidates:
        return None

    mentioned = [c for c in candidates if c[1]]
    if mentioned:
        return max(mentioned, key=lambda x: x[2])[0]

    # No name match — don't guess, let the voice description drive generation
    return None


# ── Public API ─────────────────────────────────────────────────────────────────

def generate_for_project(folder_path: str | Path, description: str = None) -> str:
    """
    Read TASK.md from folder_path, generate index.html in that folder, open in browser.
    Returns a speech string.
    """
    folder = Path(folder_path)

    # Read task description from the project folder
    if description is None:
        for name in ("TASK.md", "task.md", "Task.md", "README.md"):
            f = folder / name
            if f.exists():
                description = f.read_text(encoding="utf-8", errors="ignore").strip()
                print(f"[G.I.L. WEBGEN] Read task from {f.name}: {description[:80]}")
                break

    if not description:
        return (f"I found the {folder.name} folder but there's no TASK.md in it. "
                "Add one describing what you want and ask me again.")

    html = _generate_html(description)
    if html.startswith("ERROR:"):
        return html[6:].strip()

    out = folder / "index.html"
    try:
        out.write_text(html, encoding="utf-8")
        print(f"[G.I.L. WEBGEN] Saved -> {out}")
    except Exception as exc:
        return f"Website generated but couldn't save to {folder.name} — {exc}."

    _open_file(out)

    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = m.group(1).strip() if m else folder.name.replace("-", " ").title()

    return f"Done — '{title}' is open in your browser. index.html saved in {folder.name}."


def generate(utterance: str) -> str:
    """
    Generate a website purely from a voice command — no project folder needed.
    Extracts the real subject ('coffee shop' from 'build me a website for a coffee shop'),
    generates HTML, saves to ~/Documents/GIL_Websites/<subject>/index.html, opens browser.
    Returns a speech string, or asks for clarification if the subject is too vague.
    """
    description = _extract_description(utterance)
    if not description:
        return "What kind of website do you want? Tell me more — for example, 'a coffee shop website' or 'a portfolio for a designer'."

    html = _generate_html(description)
    if html.startswith("ERROR:"):
        return html[6:].strip()

    slug   = _sanitize(description)
    folder = _OUT_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)
    path   = folder / "index.html"
    try:
        path.write_text(html, encoding="utf-8")
        print(f"[G.I.L. WEBGEN] Saved -> {path}")
    except Exception as exc:
        return f"Website generated but couldn't save — {exc}."

    _open_file(path)

    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = m.group(1).strip() if m else description.title()

    return f"Done — '{title}' is open in your browser."
