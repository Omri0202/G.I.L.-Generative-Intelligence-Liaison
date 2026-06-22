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
You are a world-class front-end developer and award-winning UI/UX designer. \
Generate a COMPLETE, STUNNING, production-quality single-file HTML website that feels like it was built by a top design agency. \
Output ONLY raw HTML — start with <!DOCTYPE html>. No markdown, no fences, no explanation.

══ NON-NEGOTIABLE RULES ══
• Single file: <style> in <head>, ALL <script> tags before </body>.
• Real, specific copy everywhere — invent names, stories, stats, testimonials, details. NEVER Lorem Ipsum.
• Fully responsive: CSS Grid + Flexbox, mobile-first breakpoints at 1024px, 768px, 480px.
• Let the subject drive EVERY decision — sections, color palette, typography, copy, imagery, animations.
• Include pricing/payment sections ONLY when the prompt explicitly mentions prices, courses, products, or selling.
• Use CSS custom properties (--variables) for the entire design system.
• Every section must have a smooth entrance animation. Nothing static.

══ CDN LIBRARIES — ALWAYS INCLUDE ALL OF THESE ══

In <head>:
<!-- Google Fonts — always pick a premium pairing, loaded here -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<!-- Example: replace with the right pairing for the subject -->
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@400;500;700&family=Lato:wght@300;400&display=swap" rel="stylesheet">

<!-- Font Awesome 6 -->
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">

<!-- Swiper carousel -->
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.css">

Before </body> — in this exact order:
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js"></script>

══ GSAP ANIMATIONS — USE INSTEAD OF CSS TRANSITIONS FOR KEY ELEMENTS ══
Register ScrollTrigger once:
<script>gsap.registerPlugin(ScrollTrigger);</script>

Hero entrance (always):
<script>
gsap.timeline().from('.hero-title', {y:60,opacity:0,duration:1,ease:'power3.out'})
               .from('.hero-sub',   {y:40,opacity:0,duration:0.8,ease:'power3.out'},'-=0.6')
               .from('.hero-cta',   {y:30,opacity:0,duration:0.7,ease:'power3.out'},'-=0.5');
</script>

Scroll-triggered section reveals (use on every card/feature/stat):
<script>
gsap.utils.toArray('.reveal').forEach(el => {
  gsap.from(el, {y:50,opacity:0,duration:0.8,ease:'power2.out',
    scrollTrigger:{trigger:el,start:'top 88%',toggleActions:'play none none none'}});
});
gsap.utils.toArray('.reveal-left').forEach(el => {
  gsap.from(el, {x:-60,opacity:0,duration:0.9,ease:'power2.out',
    scrollTrigger:{trigger:el,start:'top 88%'}});
});
gsap.utils.toArray('.reveal-right').forEach(el => {
  gsap.from(el, {x:60,opacity:0,duration:0.9,ease:'power2.out',
    scrollTrigger:{trigger:el,start:'top 88%'}});
});
gsap.utils.toArray('.stagger-children').forEach(parent => {
  gsap.from(parent.children, {y:40,opacity:0,duration:0.7,stagger:0.12,ease:'power2.out',
    scrollTrigger:{trigger:parent,start:'top 85%'}});
});
</script>

Add class="reveal" to section headings, cards, feature blocks.
Add class="stagger-children" to grid containers so children animate in sequence.
Add class="reveal-left"/"reveal-right" to split-layout image/text pairs.

Parallax hero bg (for sites with cinematic hero images):
<script>
gsap.to('.hero', {backgroundPositionY:'30%',
  scrollTrigger:{trigger:'.hero',start:'top top',end:'bottom top',scrub:true}});
</script>

Counter animation for stats sections:
<script>
document.querySelectorAll('.stat-number').forEach(el => {
  const target = parseInt(el.dataset.target);
  gsap.from({val:0},{val:target,duration:2,ease:'power1.out',
    scrollTrigger:{trigger:el,start:'top 80%'},
    onUpdate:function(){el.textContent=Math.round(this.targets()[0].val).toLocaleString()+(el.dataset.suffix||'')}});
});
</script>
Usage: <span class="stat-number" data-target="12000" data-suffix="+">12,000+</span>

══ ALPINE.JS PATTERNS ══
Navbar mobile menu:
<nav x-data="{open:false}">
  <button @click="open=!open" :aria-expanded="open"><i class="fas fa-bars"></i></button>
  <ul :class="open?'show':''">...</ul>
</nav>

FAQ accordion (use whenever prompt mentions FAQ or questions):
<div x-data="{active:null}" class="faq-list">
  <div class="faq-item" x-data="{id:1}">
    <button @click="active=active===id?null:id" :class="active===id?'open':''">
      <span>Question here?</span><i class="fas fa-chevron-down"></i>
    </button>
    <div x-show="active===id" x-transition:enter="slide-down" x-collapse>
      <p>Answer here.</p>
    </div>
  </div>
</div>

Tabs (for menus, features, etc.):
<div x-data="{tab:'one'}">
  <div class="tabs"><button @click="tab='one'" :class="tab==='one'?'active':''">Tab 1</button>...</div>
  <div x-show="tab==='one'">Content 1</div>
</div>

══ SWIPER CAROUSELS ══
Testimonials (ALWAYS use Swiper, never static cards):
<div class="swiper testimonial-swiper">
  <div class="swiper-wrapper">
    <div class="swiper-slide"><div class="testimonial-card">...avatar, quote, name, result...</div></div>
  </div>
  <div class="swiper-pagination"></div>
</div>
<script>new Swiper('.testimonial-swiper',{loop:true,autoplay:{delay:4500,disableOnInteraction:false},
  pagination:{el:'.swiper-pagination',clickable:true},spaceBetween:24});</script>

Gallery slider (for portfolio/gallery sections):
<div class="swiper gallery-swiper">
  <div class="swiper-wrapper">...</div>
  <div class="swiper-button-next"></div><div class="swiper-button-prev"></div>
</div>
<script>new Swiper('.gallery-swiper',{slidesPerView:'auto',centeredSlides:true,
  spaceBetween:20,navigation:{nextEl:'.swiper-button-next',prevEl:'.swiper-button-prev'}});</script>

══ PAYMENT BUTTONS ══
Gumroad (simplest, no backend, overlay checkout):
<script src="https://gumroad.com/js/gumroad.js"></script>
<a class="gumroad-button" href="https://[your-store].gumroad.com/l/[product-id]">Enroll — $XX</a>

══ PHOTOGRAPHY — LOREMFLICKR ══
Every image: https://loremflickr.com/WIDTH/HEIGHT/KEYWORD1,KEYWORD2?lock=N
• KEYWORDS: 1–3 precise words for the subject (drumming→"drums,percussion"; yoga→"yoga,meditation"; coffee→"coffee,cafe")
• lock=N: unique integer 1–99 per image — NEVER reuse the same lock
• Sizes: Hero bg 1920/1080 | Wide 1200/800 | Card 800/600 | Portrait 600/900 | Square 600/600
• CSS hero: background: linear-gradient(rgba(0,0,0,0.55),rgba(0,0,0,0.3)), url('https://loremflickr.com/1920/1080/KEYWORDS?lock=1') center/cover no-repeat fixed;
• All <img>: add loading="lazy" except above-fold
• Aim 8–16 images. Never use picsum, placeholder.com, unsplash.

══ ADAPTIVE SECTION STRUCTURE ══

Music / Band:
  Hero → Story/Origins → Discography (album cards with photos) → Tour Dates → Gallery (masonry) → Members → Footer

Art / Photography / Creative portfolio:
  Full-screen hero → Gallery grid (masonry, hover zoom) → Artist statement → Selected works → Exhibitions → Contact → Footer

Nature / Travel / Lifestyle:
  Cinematic hero → Destinations/Topics grid → Featured story (image+text split) → Gallery → Tips → Newsletter → Footer

Restaurant / Cafe / Food:
  Atmospheric hero → Story → Menu by category (Alpine.js tabs) → Gallery → Chef/Team → Location+hours → Footer

Personal / Blog / Writer:
  Bold typographic hero → About (photo+bio) → Featured posts (large cards) → Topics → Newsletter → Contact → Footer

Tech / Product / App:
  Hero with mockup → Problem statement → Features (icon cards) → How it works (numbered steps) → Screenshots → Testimonials (Swiper) → Pricing → CTA → Footer

Event / Festival / Conference:
  Countdown hero → Lineup (cards+photos) → Schedule → Venue (photo+map) → Past highlights gallery → Sponsors → Tickets → Footer

Nonprofit / Community / Cause:
  Emotional hero → Mission → Impact stats → Stories (quote cards) → Team → How to help → Donate → Footer

Fitness / Sports / Wellness:
  Dynamic action hero → Programs (photo cards) → Transformations gallery → Schedule → Trainers (portrait cards) → Testimonials (Swiper) → Join CTA → Footer

Course / Education / Coaching (use when prompt mentions courses, lessons, coaching, or prices):
  Sticky nav (logo + "Enroll Now" CTA) →
  Hero (bold headline, subheadline, primary CTA, coach photo split-right) →
  Social proof bar (3–4 stats: student count, rating, guarantee, credential) →
  What You'll Learn (3-col icon grid, 6–9 bullet points) →
  Pricing Cards (3 columns with exact prices from prompt — middle card highlighted as "Most Popular" with accent border + badge; each card: price large, feature checklist 4–6 items, Gumroad enroll button) →
  Testimonials (Swiper carousel — photo + name + city + specific result) →
  Instructor Bio (photo + credentials + story paragraph) →
  FAQ (Alpine.js accordion, 5 questions covering: beginner-friendliness, access duration, satisfaction guarantee, live vs recorded, equipment needed) →
  Final CTA banner (big headline + 2 buttons) →
  Footer (links, refund policy, contact email)

E-commerce / Product Store:
  Hero with product showcase → Products grid (photo cards + price + Add to Cart) → Categories → Benefits → Reviews → Newsletter → Footer

REQUIRED in every site: nav, hero, footer. Everything else: choose what fits.

══ VISUAL IDENTITY — MATCH THE SUBJECT PRECISELY ══
Sports/Energy:     near-black bg, electric neon accent (yellow #FAFF00, orange #FF6B00, or red #FF2D55)
Music/Nightlife:   #0A0A0F bg, electric accent (purple #9B5DE5, cyan #00F5D4, or hot-pink #FF006E)
Nature/Wellness:   warm cream #FAF7F0 or forest #1A2F1A, earthy accent (terracotta #C1541A, sage #7D9B76)
Art/Creative:      bold contrast, vivid accent (orange #FF6B35, crimson #DC143C, violet #8A2BE2)
Food/Hospitality:  rich warm bg (#1A0A00), amber/red accent (#F4A261), cream text
Tech/SaaS:         #07080F dark, electric blue-violet gradient accent (#4361EE → #7209B7)
Luxury/Fashion:    #0D0D0D or #FAF8F5 cream, gold (#C9A96E) or silver (#C0C0C0) accent, maximum whitespace
Personal/Blog:     white/light bg, soft single accent, serif headings
Fitness/Sports:    dark dramatic bg, high-contrast neon accent, bold condensed type
Education/Course:  trustworthy navy/white, blue accent, professional and credible

Typography pairs (always load from Google Fonts — pick the right one for the vibe):
• Sports/Bold:    "Bebas Neue" display + "Inter" body
• Tech/Modern:    "Space Grotesk" display + "Lato" body
• Luxury/Elegant: "Cormorant Garamond" display + "Montserrat" body
• Creative/Art:   "DM Serif Display" + "DM Sans"
• Warm/Friendly:  "Plus Jakarta Sans" (all weights) alone
• Classic:        "Playfair Display" + "Inter"

══ CSS ARCHITECTURE ══
:root {
  /* Background depth system — 3 levels */
  --bg:     #...;  /* page background */
  --bg2:    #...;  /* slightly lighter — cards, panels */
  --bg3:    #...;  /* even lighter — hover states, nested */
  --card:   #...;  /* card background */
  --border: rgba(255,255,255,0.08);  /* adjust for light themes */

  /* Accent system */
  --accent:     #...;
  --accent2:    #...;  /* secondary/hover accent */
  --accent-rgb: R,G,B;  /* for rgba() use */

  /* Typography */
  --text:  #...;
  --muted: #...;

  /* Layout */
  --radius:   14px;
  --radius-lg: 24px;
  --section-pad: clamp(60px, 10vw, 120px);
  --container: min(1200px, 92vw);

  /* Shadows */
  --shadow:    0 4px 24px rgba(0,0,0,0.18);
  --shadow-lg: 0 12px 48px rgba(0,0,0,0.28);
  --glow:      0 0 32px rgba(var(--accent-rgb),0.35);
}
html { scroll-behavior: smooth; }
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background:var(--bg); color:var(--text); font-family:'Your Body Font',sans-serif; overflow-x:hidden; }

/* Gradient text — use on hero headline accent word */
.gradient-text {
  background: linear-gradient(135deg, var(--accent), var(--accent2));
  -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* Glassmorphism card */
.glass {
  background: rgba(255,255,255,0.04);
  backdrop-filter: blur(12px) saturate(180%);
  border: 1px solid rgba(255,255,255,0.08);
  border-radius: var(--radius-lg);
}

/* Button styles */
.btn-primary {
  display:inline-flex; align-items:center; gap:8px;
  padding:14px 32px; border-radius:50px; font-weight:700;
  background:var(--accent); color:#000; text-decoration:none;
  transition:transform 0.2s, box-shadow 0.2s;
}
.btn-primary:hover { transform:translateY(-2px); box-shadow:var(--glow); }
.btn-ghost {
  display:inline-flex; align-items:center; gap:8px;
  padding:13px 30px; border-radius:50px; font-weight:600;
  border:1.5px solid var(--accent); color:var(--accent);
  text-decoration:none; transition:all 0.2s;
}
.btn-ghost:hover { background:var(--accent); color:#000; }

/* Scrollbar */
::-webkit-scrollbar { width:6px; }
::-webkit-scrollbar-track { background:var(--bg2); }
::-webkit-scrollbar-thumb { background:var(--accent); border-radius:3px; }

══ NAVBAR ══
position:fixed; top:0; width:100%; z-index:1000; height:68px;
backdrop-filter:blur(20px) saturate(180%); background:rgba(bg,0.82);
border-bottom:1px solid var(--border);
JS — add scrolled class: document.addEventListener('scroll',()=>nav.classList.toggle('scrolled',scrollY>80));
.scrolled { box-shadow:0 2px 20px rgba(0,0,0,0.3); }
Mobile hamburger: Alpine.js x-data="{open:false}"

══ HERO ══
min-height:100vh; display:flex; align-items:center; position:relative; overflow:hidden;
Full-bleed loremflickr background + dark gradient overlay.
Headline: font-size:clamp(3rem,7vw,7rem); font-weight:900; line-height:1.05; letter-spacing:-0.03em
— wrap key word in <span class="gradient-text">
Subheadline: max-width:560px; font-size:clamp(1.1rem,2.5vw,1.35rem); color:var(--muted); line-height:1.7
CTA row: flex gap with .btn-primary + .btn-ghost
Trust badges (star ratings, user counts, logos) below CTA for social proof
GSAP hero entrance: hero-title → hero-sub → hero-cta → trust badges (staggered)

══ SECTION DESIGN ══
Every section: padding:var(--section-pad) 5vw; max-width:var(--container); margin:0 auto;
Section labels: small uppercase tracking-widest text in accent color ABOVE headings
Section headings: clamp(2rem,5vw,3.5rem), font-weight:800
Subtle dividers between sections: 1px border or gradient fade

Feature cards: glass morphism + hover lift (transform:translateY(-6px) + box-shadow:var(--shadow-lg))
Stats: huge number in accent + label — always animate with GSAP counter
Team/product cards: image (round or square) + hover overlay with links

══ PRICING CARDS ══
3-col CSS grid → 1-col on mobile
Middle card: ring border (var(--accent)), "Most Popular" badge (position:absolute;top:-14px)
Price: font-size:3.5rem; font-weight:900; — period + cents in smaller size
Feature list: <i class="fas fa-check" style="color:var(--accent)"></i> per feature
CTA button: full-width .btn-primary

══ FOOTER ══
padding:80px 5vw 32px; border-top:1px solid var(--border);
Grid: brand col (logo + tagline + socials) | nav columns | newsletter/contact
Social icons: Font Awesome brands relevant to subject
Bottom row: copyright (centered) + policy links

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
            messages=[{"role": "user", "content": _build_user_prompt(description)}],
        )
        return msg.content[0].text.strip()
    except Exception as exc:
        print(f"[G.I.L. WEBGEN] Claude error: {exc} — falling back to Groq.")
        keys = _groq_keys()
        if keys:
            return _generate_groq(description, keys)
        return f"ERROR: Claude failed and no Groq fallback — {exc.__class__.__name__}."


def _generate_groq(description: str, api_keys: list[str]) -> str:
    """Groq fallback — tries multiple models so one rate-limited model doesn't block all."""
    import requests as _rq
    import time as _time
    _GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    # Each model has its own independent rate-limit quota on Groq
    _MODELS = ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "gemma2-9b-it"]
    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",   "content": _build_user_prompt(description)},
    ]
    for model in _MODELS:
        print(f"[G.I.L. WEBGEN] Trying Groq model: {model}...")
        payload = {"model": model, "messages": messages, "max_tokens": 8000, "temperature": 0.75}
        for key in api_keys:
            hdrs = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            try:
                resp = _rq.post(_GROQ_URL, json=payload, headers=hdrs, timeout=120)
                if resp.status_code == 429:
                    print(f"[G.I.L. WEBGEN] {model} rate-limited, trying next model/key...")
                    continue
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"].strip()
            except _rq.exceptions.Timeout:
                print(f"[G.I.L. WEBGEN] {model} timed out.")
                continue
            except Exception as exc:
                print(f"[G.I.L. WEBGEN] {model} error: {exc}")
                continue
    return "ERROR: Groq failed on all models and keys — wait a minute and try again."


_GENERIC_WORDS = {
    "website", "webpage", "page", "site", "web", "app", "application",
    "build", "create", "make", "generate", "design", "write",
    "your", "mine", "just", "please", "could", "would", "want",
    "the", "for", "and", "with", "that", "this", "some", "need",
}


def _extract_description(utterance: str) -> str:
    """
    Pull the full description out of a voice command — preserving ALL detail
    (prices, design requests, course names, etc.), not just the bare subject.
    'build me a website about tennis with courses from 10 20 30 dollars'
      ->  'tennis with courses from 10 20 30 dollars'
    """
    t = utterance.strip()
    # Try: grab everything after "for [a/my]" or "about [a/my]"
    m = re.search(r"\b(?:for|about)\s+(?:my\s+|a\s+|an\s+|the\s+)?(.+)$", t, re.IGNORECASE)
    if m:
        subject = m.group(1).strip()
        # Drop a trailing bare "website/page/app" if it leaked in with nothing after it
        subject = re.sub(
            r"\s*(website|webpage|web\s+page|landing\s+page|web\s+app|site|page)\s*$",
            "", subject, flags=re.IGNORECASE,
        ).strip()
        if len(subject) > 2:
            return subject

    # Fallback: strip the action + "website" wrapper from the front only
    cleaned = re.sub(
        r"^(?:please\s+)?(?:can\s+you\s+|could\s+you\s+)?"
        r"(?:build|create|make|generate|design|write)\s+"
        r"(?:me\s+)?(?:a\s+|an\s+)?"
        r"(?:website|webpage|web\s+page|landing\s+page|web\s+app|site)?\s*",
        "", t, flags=re.IGNORECASE,
    ).strip()
    return cleaned if len(cleaned) > 2 else ""


def _build_user_prompt(description: str) -> str:
    """
    Build a structured, explicit prompt from the description.
    Extracts prices, detects course/selling intent, and passes fully structured
    tier definitions to the LLM so it produces exactly what was asked for.
    """
    # Extract dollar amounts: "$10", "10 dollars", "10, 20, 30 dollars"
    prices = re.findall(r"\$?\s*(\d+(?:\.\d{1,2})?)\s*(?:dollars?|usd|\$)?", description, re.IGNORECASE)
    prices = [p for p in prices if 1.0 <= float(p) <= 10000.0]

    # Extract first content keyword (e.g. "tennis" from "tennis with courses...")
    first_word = description.split()[0] if description.split() else "topic"

    is_course = bool(re.search(
        r"\b(course|courses|lesson|lessons|coaching|class|classes|program|programs|curriculum|training)\b",
        description, re.IGNORECASE,
    ))
    is_selling = bool(re.search(
        r"\b(sell|selling|buy|shop|store|purchase|payment|checkout|enroll|price|pricing)\b",
        description, re.IGNORECASE,
    ))
    is_course_sell = (is_course or is_selling) and len(prices) > 0

    lines = [f"Build a complete, production-quality website for: {description}\n"]

    if is_course_sell:
        tier_names  = ["Starter", "Pro", "Elite"]
        tier_desc   = [
            ["5 video lessons", "PDF guide", "Lifetime access", "Beginner-friendly"],
            ["12 video lessons", "PDF guide + workbook", "1x coaching call", "Lifetime access", "Private community"],
            ["20 video lessons", "Full resource library", "3x coaching calls", "Lifetime access", "Private community", "Certificate of completion"],
        ]
        tiers = []
        for i, price in enumerate(prices[:3]):
            name  = tier_names[i] if i < len(tier_names) else f"Tier {i+1}"
            feats = tier_desc[i] if i < len(tier_desc) else ["Full access", "Lifetime access"]
            tiers.append(f"  - {name} (${price}): {' | '.join(feats)}")
        lines.append("PRICING TIERS (use these exact prices and names):")
        lines.extend(tiers)
        lines.append("Middle tier = 'Most Popular' with accent border and badge.")
        lines.append("Each card needs: large price, feature checklist with checkmark icons, Gumroad enroll button.")
        lines.append("Gumroad button: <a class=\"gumroad-button\" href=\"https://[your-store].gumroad.com/l/[product-id]\">Enroll Now — $XX</a>")
        lines.append("Include <script src=\"https://gumroad.com/js/gumroad.js\"></script> in <head>.\n")

    if is_course_sell:
        subject = re.sub(
            r"\s*(with|and|from|for|about|courses?|lessons?|coaching|classes?|programs?|pricing?|price|sell|selling).*$",
            "", description, flags=re.IGNORECASE,
        ).strip() or first_word
        lines.append(f"SITE TYPE: Course-selling landing page for '{subject}' courses.")
        lines.append("SECTIONS IN THIS ORDER:")
        lines.append("  1. Sticky nav — logo left, 'Enroll Now' button right (scrolls to #pricing)")
        lines.append("  2. Hero — bold headline ('Master [Subject] in 30 Days'), subheadline, CTA button, coach photo split-right, loremflickr hero bg")
        lines.append("  3. Social proof bar — 4 stats: student count, star rating, money-back guarantee, coach credential")
        lines.append("  4. What You'll Learn — 3-column icon grid (Font Awesome icons), 6–9 specific skills")
        lines.append("  5. Pricing cards — 3 columns, exact tiers above, id='pricing'")
        lines.append("  6. Testimonials — Swiper carousel, 3 quotes with name + city + specific metric result")
        lines.append("  7. Instructor bio — portrait photo, credentials, personal story paragraph")
        lines.append("  8. FAQ — Alpine.js accordion, 5 questions (beginner-friendly? / access duration? / refund policy? / live or recorded? / equipment needed?)")
        lines.append("  9. Final CTA banner — big headline, 2 buttons")
        lines.append(" 10. Footer — links, refund policy, contact email, social icons\n")
    else:
        lines.append(f"Choose the section structure that best fits '{description}' — not a generic commercial template.\n")

    # Loremflickr keyword guidance
    kw = first_word.lower()
    lines.append(f"IMAGES: Use loremflickr.com for ALL images. Primary keyword: '{kw}' (e.g. https://loremflickr.com/800/600/{kw},sport?lock=2).")
    lines.append("Use a DIFFERENT lock number (1–99) for every image. Hero bg uses lock=1.")
    lines.append("Color palette, typography, and energy must match this specific subject.")

    return "\n".join(lines)


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
