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

_SYSTEM = """You are an elite front-end developer and UI/UX designer building award-winning websites.
Generate a COMPLETE, STUNNING, production-ready single-file HTML website.
Output ONLY raw HTML starting with <!DOCTYPE html>. No markdown, no fences, no explanation.

ABSOLUTE RULES
- Single HTML file: <style> in <head>, all <script> before </body>.
- Real, specific content — invent names, stories, stats, testimonials. ZERO Lorem Ipsum.
- Fully responsive: mobile-first, breakpoints at 1024px, 768px, 480px.
- IMAGES: use ONLY the exact image paths provided in the request (img1, img2, img3...).
  Hero bg: background: linear-gradient(to bottom,rgba(0,0,0,.6),rgba(0,0,0,.2)), url(img1) center/cover no-repeat fixed;
  If more images needed than provided, reuse the provided ones with different CSS effects.

ALWAYS IN <head>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
[correct Google Fonts pairing for the subject]
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.css">

ALWAYS BEFORE </body>
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js"></script>
<script>
gsap.registerPlugin(ScrollTrigger);
gsap.timeline()
  .from('.hero-title',{y:70,opacity:0,duration:1.1,ease:'power3.out'})
  .from('.hero-sub',  {y:45,opacity:0,duration:0.9,ease:'power3.out'},'-=0.7')
  .from('.hero-cta',  {y:30,opacity:0,duration:0.8,ease:'power3.out'},'-=0.6');
gsap.utils.toArray('.reveal').forEach(el=>
  gsap.from(el,{y:55,opacity:0,duration:0.9,ease:'power2.out',
    scrollTrigger:{trigger:el,start:'top 88%'}}));
gsap.utils.toArray('.stagger').forEach(p=>
  gsap.from(p.children,{y:40,opacity:0,duration:0.8,stagger:0.13,ease:'power2.out',
    scrollTrigger:{trigger:p,start:'top 86%'}}));
document.querySelectorAll('[data-count]').forEach(el=>{
  const end=+el.dataset.count,suf=el.dataset.suf||'';
  gsap.from({v:0},{v:end,duration:2.2,ease:'power1.out',
    scrollTrigger:{trigger:el,start:'top 80%'},
    onUpdate(){el.textContent=Math.round(this.targets()[0].v).toLocaleString()+suf}});
});
</script>

CSS DESIGN SYSTEM (always use :root variables)
:root {
  --bg:#0A0A0F; --bg2:#111118; --bg3:#1A1A25; --card:#13131E;
  --border:rgba(255,255,255,0.07);
  --accent:#YOUR_COLOR; --accent2:#YOUR_COLOR2; --accent-rgb:R,G,B;
  --text:#F0F0FF; --muted:rgba(200,200,220,0.65);
  --radius:14px; --radius-lg:24px;
}
[Override entire palette for light-theme subjects: wellness, blog, portfolio]

/* Gradient text — wrap key headline word in this span */
.gradient-text{background:linear-gradient(135deg,var(--accent),var(--accent2));
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}

/* Glassmorphism card */
.glass{background:rgba(255,255,255,0.04);backdrop-filter:blur(12px) saturate(180%);
  border:1px solid var(--border);border-radius:var(--radius-lg);}

/* Buttons */
.btn-primary{display:inline-flex;align-items:center;gap:8px;padding:15px 36px;border-radius:50px;
  background:var(--accent);color:#000;font-weight:700;text-decoration:none;
  transition:transform .2s,box-shadow .2s;}
.btn-primary:hover{transform:translateY(-2px);box-shadow:0 0 32px rgba(var(--accent-rgb),.45);}
.btn-ghost{display:inline-flex;align-items:center;gap:8px;padding:14px 34px;border-radius:50px;
  border:2px solid var(--accent);color:var(--accent);text-decoration:none;font-weight:600;
  transition:all .2s;}
.btn-ghost:hover{background:var(--accent);color:#000;}

NAVBAR (always fixed)
position:fixed;top:0;width:100%;z-index:1000;height:66px;
backdrop-filter:blur(18px) saturate(200%);background:rgba(10,10,20,0.82);
border-bottom:1px solid var(--border);
JS: document.addEventListener('scroll',()=>nav.classList.toggle('scrolled',scrollY>80));
Alpine mobile: x-data="{open:false}" on <nav>

HERO (always 100vh, always stunning)
Headline: font-size:clamp(3.2rem,8vw,7.5rem);font-weight:900;letter-spacing:-0.04em;line-height:1.0
Subheadline: max-width:580px;font-size:clamp(1.1rem,2.5vw,1.4rem);color:var(--muted);line-height:1.7
CTA row: btn-primary + btn-ghost + trust line (star rating + user count)
GSAP entrance applied via hero-title, hero-sub, hero-cta classes.

CARDS & SECTIONS
Feature cards: glass + large Font Awesome icon (var(--accent)) + title + description
Image cards: provided image, hover zoom (transform:scale(1.06);transition:.4s), gradient overlay, title
Stats: <span data-count="12000" data-suf="+">0</span> — animated by GSAP counter
Add class="reveal" to headings, class="stagger" to card grids for auto-animation.

SWIPER TESTIMONIALS (always carousel, never static)
<div class="swiper testimonials"><div class="swiper-wrapper">[slides with photo, quote, name, result]
</div><div class="swiper-pagination"></div></div>
<script>new Swiper('.testimonials',{loop:true,autoplay:{delay:4500,disableOnInteraction:false},
  pagination:{el:'.swiper-pagination',clickable:true},spaceBetween:24});</script>

ALPINE FAQ
<div x-data="{a:null}"><div><button @click="a=a===1?null:1">Q?<i class="fas fa-chevron-down"></i></button>
<div x-show="a===1" x-transition><p>Answer.</p></div></div></div>

PRICING CARDS (only when prices given)
3-column grid; middle card accent border + "Most Popular" badge; Gumroad button.
<script src="https://gumroad.com/js/gumroad.js"></script>
<a class="gumroad-button" href="https://[store].gumroad.com/l/[id]">Enroll $XX</a>

FOOTER
Dark bg; grid: brand+socials | nav links | newsletter; FA brand icons; copyright line.

COLOR BY SUBJECT
Sports/Fitness:   #080808 bg, electric yellow #FAFF00 or neon green #39FF14 accent
Music/Nightlife:  #06060F bg, purple #9B5DE5 or cyan #00F5D4 accent
Nature/Wellness:  #FAF7F0 bg (LIGHT), terracotta #C1541A accent, dark text (full light theme)
Tech/SaaS:        #080A14 bg, blue #4361EE to violet #7209B7 gradient accent
Food/Restaurant:  #0D0800 bg, amber #F4A261 accent, cream text
Art/Creative:     #050505 bg, orange #FF6B35 or crimson #DC143C accent
Luxury/Fashion:   #0A0A08 or #FAF8F4 bg, gold #C9A96E accent, Cormorant Garamond
Personal/Blog:    #FAFAFA bg (LIGHT), single dark accent, clean minimal

FONT PAIRING (Google Fonts — pick the right one for the subject)
Sports:   Bebas Neue + Inter
Tech:     Space Grotesk:wght@400;500;700 + Lato:wght@300;400;700
Luxury:   Cormorant+Garamond:wght@400;600 + Montserrat:wght@400;500
Creative: DM+Serif+Display + DM+Sans:wght@400;500;600
Warm:     Plus+Jakarta+Sans:wght@400;500;600;700
Classic:  Playfair+Display:wght@700;800 + Inter:wght@400;500;600

OUTPUT: Only the HTML. Start with <!DOCTYPE html>.
"""


# ── Image pre-generation (Pollinations.ai, concurrent) ───────────────────────

def _image_prompts(description: str) -> list[tuple[str, str, int, int]]:
    """(prompt, filename, w, h) for each image needed."""
    return [
        (f"Cinematic hero photograph for {description}, dramatic professional lighting, ultra sharp, 4k", "hero.jpg", 1920, 1080),
        (f"Beautiful lifestyle photo for {description}, warm natural light, professional photography", "photo1.jpg", 900, 700),
        (f"Atmospheric close-up related to {description}, high quality, vibrant colors", "photo2.jpg", 800, 800),
        (f"Professional detail shot representing {description}, product photography quality", "photo3.jpg", 800, 600),
    ]


def _generate_site_images(description: str, img_dir: Path) -> dict[str, str]:
    """
    Generate real AI images concurrently using Pollinations FLUX.
    Returns {filename: './images/filename'} for HTML injection.
    Runs in parallel with HTML generation so total time is ~30s, not 60s.
    """
    import concurrent.futures, shutil
    img_dir.mkdir(parents=True, exist_ok=True)

    def _gen(item: tuple) -> tuple[str, str | None]:
        prompt, filename, w, h = item
        try:
            from image_gen import generate as _img
            src = _img(prompt, width=w, height=h, model="flux")
            shutil.copy2(str(src), str(img_dir / filename))
            return filename, f"./images/{filename}"
        except Exception as exc:
            print(f"[G.I.L. WEBGEN] image failed ({filename}): {exc}")
            return filename, None

    result: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        for fname, path in ex.map(_gen, _image_prompts(description)):
            if path:
                result[fname] = path
    print(f"[G.I.L. WEBGEN] Generated {len(result)} images")
    return result



# ── Helpers ───────────────────────────────────────────────────────────────────

def _anthropic_key() -> str:
    return os.getenv("ANTHROPIC_API_KEY", "")

def _groq_keys() -> list[str]:
    return [k for k in [os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")] if k]

def _sanitize(text: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_]+", "_", slug).strip("_")
    return slug[:40] or "website"

def _open_file(path: Path) -> None:
    import ctypes
    ret = ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), None, None, 1)
    if ret <= 32:
        webbrowser.open(path.as_uri())

def _generate_claude(user_prompt: str, api_key: str) -> str:
    try:
        import anthropic
        print(f"[G.I.L. WEBGEN] Using Claude ({_CLAUDE_MODEL})...")
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=_CLAUDE_MODEL, max_tokens=8096, system=_SYSTEM,
            messages=[{"role": "user", "content": user_prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"[G.I.L. WEBGEN] Claude failed: {exc}")
        keys = _groq_keys()
        return _generate_groq(user_prompt, keys) if keys else f"ERROR: {exc}"

def _generate_groq(user_prompt: str, api_keys: list[str]) -> str:
    import requests as _rq
    _GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    _MODELS   = ["llama-3.3-70b-versatile", "gemma2-9b-it"]
    messages  = [{"role": "system", "content": _SYSTEM},
                 {"role": "user",   "content": user_prompt}]
    for model in _MODELS:
        print(f"[G.I.L. WEBGEN] Trying {model}...")
        payload = {"model": model, "messages": messages,
                   "max_tokens": 8000, "temperature": 0.7}
        for key in api_keys:
            hdrs = {"Authorization": f"Bearer {key}",
                    "Content-Type": "application/json"}
            try:
                resp = _rq.post(_GROQ_URL, json=payload, headers=hdrs, timeout=120)
                if resp.status_code == 429:
                    continue
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except _rq.exceptions.Timeout:
                continue
            except Exception as exc:
                print(f"[G.I.L. WEBGEN] {model}: {exc}")
    return "ERROR: Groq failed on all models — wait a minute and try again."

_GENERIC_WORDS = {
    "website","webpage","page","site","web","app","application",
    "build","create","make","generate","design","write",
    "your","mine","just","please","could","would","want",
    "the","for","and","with","that","this","some","need",
}

def _extract_description(utterance: str) -> str:
    t = utterance.strip()
    m = re.search(r"\b(?:for|about)\s+(?:my\s+|a\s+|an\s+|the\s+)?(.+)$", t, re.IGNORECASE)
    if m:
        subject = m.group(1).strip()
        subject = re.sub(
            r"\s*(website|webpage|web\s+page|landing\s+page|web\s+app|site|page)\s*$",
            "", subject, flags=re.IGNORECASE,
        ).strip()
        if len(subject) > 2:
            return subject
    cleaned = re.sub(
        r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
        r"(?:build|create|make|generate|design|write)\s+"
        r"(?:me\s+)?(?:a\s+|an\s+)?"
        r"(?:website|webpage|web\s+page|landing\s+page|web\s+app|site)?\s*",
        "", t, flags=re.IGNORECASE,
    ).strip()
    return cleaned if len(cleaned) > 2 else ""

def _build_user_prompt(description: str, images: dict) -> str:
    """
    Build the user message. When real images are available, inject their paths
    so the LLM uses them instead of loremflickr or placeholders.
    Also injects pricing tiers when relevant.
    """
    lines = [f"Build a complete, production-quality website for: {description}\n"]

    # Inject real image paths when available
    if images:
        img_list = "\n".join(f"  - {name}: {path}" for name, path in images.items())
        lines.append(
            "IMPORTANT — Use ONLY these real AI-generated images (not loremflickr):\n"
            + img_list + "\n"
            "hero.jpg = hero background | photo1.jpg, photo2.jpg, photo3.jpg = section/card images\n"
            "Reference them with relative paths exactly as shown above.\n"
        )
    else:
        kw = description.split()[0].lower() if description.split() else "subject"
        lines.append(
            f"IMAGES: loremflickr.com — primary keyword '{kw}'. "
            f"Example: https://loremflickr.com/1200/800/{kw},style?lock=N "
            "(unique N 1-99 per image)\n"
        )

    # Detect pricing tiers
    prices = re.findall(r"\$?\s*(\d+(?:\.\d{1,2})?)\s*(?:dollars?|usd|\$)?",
                        description, re.IGNORECASE)
    prices = [p for p in prices if 1.0 <= float(p) <= 10_000.0]
    is_course = bool(re.search(
        r"\b(course|lesson|coaching|class|program|training)\b", description, re.IGNORECASE))
    is_selling = bool(re.search(
        r"\b(sell|shop|store|enroll|price|pricing)\b", description, re.IGNORECASE))

    if prices and (is_course or is_selling):
        tier_names = ["Starter", "Pro", "Elite"]
        tier_feats = [
            ["5 video lessons", "PDF guide", "Lifetime access"],
            ["12 video lessons", "Workbook + PDF", "1 coaching call", "Community access"],
            ["20 video lessons", "Full resource library", "3 coaching calls",
             "Community", "Certificate"],
        ]
        lines.append("PRICING TIERS:")
        for i, price in enumerate(prices[:3]):
            name  = tier_names[i] if i < 3 else f"Tier {i+1}"
            feats = tier_feats[i] if i < 3 else ["Full access", "Lifetime access"]
            lines.append(f"  {name} — ${price}: {' | '.join(feats)}")
        lines.append("Middle tier = 'Most Popular'. Use Gumroad buttons.")
        lines.append('<script src="https://gumroad.com/js/gumroad.js"></script>')
        lines.append('<a class="gumroad-button" href="https://[store].gumroad.com/l/[id]">Enroll $XX</a>\n')

    lines.append("Make it visually stunning, unique to this specific subject.")
    return "\n".join(lines)

def _find_web_project(text: str) -> Path | None:
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
                    (folder / n for n in ("TASK.md","task.md","Task.md","README.md")
                     if (folder/n).exists()), None)
                if task is None:
                    continue
                fname_words = re.split(r"[-_\s]+", folder.name.lower())
                mentioned = any(
                    w in text_lower for w in fname_words
                    if len(w) > 3 and w not in _GENERIC_WORDS)
                candidates.append((folder, mentioned, task.stat().st_mtime))
        except Exception:
            continue
    if not candidates:
        return None
    mentioned = [c for c in candidates if c[1]]
    if mentioned:
        return max(mentioned, key=lambda x: x[2])[0]
    return None


# ── Generation pipeline ───────────────────────────────────────────────────────

def _run_generation(description: str, out_folder: Path) -> str:
    """
    Core generation pipeline used by both generate() and generate_for_project():
    1. Start image generation and HTML generation concurrently.
    2. Inject real image paths into HTML prompt.
    3. Save and open.
    Returns the raw HTML string or an 'ERROR:' prefixed string.
    """
    import concurrent.futures

    img_dir = out_folder / "images"

    # Phase 1 — run image generation and (a quick design spec) concurrently
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        img_future  = ex.submit(_generate_site_images, description, img_dir)
        html_future = ex.submit(_generate_html, description, {})  # first pass, no images yet

        images = img_future.result()   # {filename: relative_path}
        html   = html_future.result()

    # If we got images AND html — re-generate HTML with image paths injected
    if images and not html.startswith("ERROR:"):
        print(f"[G.I.L. WEBGEN] Re-generating HTML with {len(images)} real images...")
        html = _generate_html(description, images)

    return html


def _generate_html(description: str, images: dict | None = None) -> str:
    """
    Core HTML generation. Passes real image paths to the LLM when available.
    Falls back to empty dict (model uses its own image strategy).
    """
    print(f"[G.I.L. WEBGEN] Generating HTML for: {description[:80]}")
    ant_key = _anthropic_key()
    user_prompt = _build_user_prompt(description, images or {})

    if ant_key:
        html = _generate_claude(user_prompt, ant_key)
    else:
        keys = _groq_keys()
        if not keys:
            return "ERROR: No API key — add GROQ_API_KEY to .env"
        html = _generate_groq(user_prompt, keys)

    if html.startswith("ERROR:"):
        return html

    html = re.sub(r"^```[^\n]*\n?", "", html)
    html = re.sub(r"\n?```\s*$", "", html).strip()

    if not html.lower().startswith("<!doctype"):
        print(f"[G.I.L. WEBGEN] Unexpected output: {html[:120]}")
        return "ERROR: AI returned unexpected content. Try rephrasing your request."

    return html


def generate_for_project(folder_path: str | Path, description: str = None) -> str:
    """Read TASK.md, generate index.html in that folder, open in browser."""
    folder = Path(folder_path)

    if description is None:
        for name in ("TASK.md", "task.md", "Task.md", "README.md"):
            f = folder / name
            if f.exists():
                description = f.read_text(encoding="utf-8", errors="ignore").strip()
                print(f"[G.I.L. WEBGEN] Task: {description[:80]}")
                break

    if not description:
        return (f"I found the {folder.name} folder but there's no TASK.md in it. "
                "Add one describing what you want and ask me again.")

    html = _run_generation(description, folder)
    if html.startswith("ERROR:"):
        return html[6:].strip()

    out = folder / "index.html"
    try:
        out.write_text(html, encoding="utf-8")
    except Exception as exc:
        return f"Website generated but couldn't save to {folder.name} — {exc}."

    _open_file(out)
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = m.group(1).strip() if m else folder.name.replace("-", " ").title()
    return f"Done — '{title}' is open in your browser."


def generate(utterance: str) -> str:
    """Generate a website from a voice command and open it in the browser."""
    description = _extract_description(utterance)
    if not description:
        return "What kind of website? For example: 'a coffee shop website' or 'a portfolio for a photographer'."

    slug   = _sanitize(description)
    folder = _OUT_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)

    html = _run_generation(description, folder)
    if html.startswith("ERROR:"):
        return html[6:].strip()

    path = folder / "index.html"
    try:
        path.write_text(html, encoding="utf-8")
    except Exception as exc:
        return f"Website generated but couldn't save — {exc}."

    _open_file(path)
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = m.group(1).strip() if m else description.title()

    return f"Done — '{title}' is open in your browser."
