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
You are a world-class front-end developer. Build a COMPLETE, STUNNING, single-file website.
Output ONLY raw HTML starting with <!DOCTYPE html>. No markdown, no fences.

RULE 1 - CONTRAST (check EVERY element):
  Dark background (hex < #666): ALL text MUST be #F0F0FF or lighter.
  Light background (hex > #AAA): ALL text MUST be #111118 or darker.
  Applies to nav, headings, body, cards, footer — EVERYTHING.

RULE 2 - IMAGES: use ONLY these exact placeholder strings:
  Hero:   HERO_IMG
  Cards:  CARD_IMG_1   CARD_IMG_2   CARD_IMG_3
  Hero CSS: background:linear-gradient(rgba(0,0,0,.62),rgba(0,0,0,.32)),url(HERO_IMG) center/cover no-repeat fixed;
  Card <img>: <img src="CARD_IMG_1" alt="..." loading="lazy">
  Do NOT use loremflickr or any other image URL.

RULE 3 - GSAP classes (required on exact elements):
  Main h1:             class="hero-title"
  Hero paragraph:      class="hero-sub"
  Hero CTA container:  class="hero-cta"
  Every section h2/h3: add class="reveal"
  Every card grid div: add class="stagger"
  Left split column:   class="reveal-left"
  Right split column:  class="reveal-right"
  Animated number:     <span data-count="12000" data-suf="+">0</span>

ALWAYS IN <head>:
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DISPLAY:wght@700;800;900&family=BODY:wght@300;400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.css">

ALWAYS BEFORE </body>:
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/gsap.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/gsap@3.12.5/dist/ScrollTrigger.min.js"></script>
<script>
gsap.registerPlugin(ScrollTrigger);
gsap.timeline({defaults:{ease:'power3.out'}})
  .from('.hero-title',{y:80,opacity:0,duration:1.2})
  .from('.hero-sub',{y:50,opacity:0,duration:1.0},'-=0.8')
  .from('.hero-cta',{y:35,opacity:0,duration:0.9},'-=0.7');
gsap.utils.toArray('.reveal').forEach(el=>
  gsap.from(el,{y:60,opacity:0,duration:1.0,ease:'power2.out',
    scrollTrigger:{trigger:el,start:'top 88%'}}));
gsap.utils.toArray('.stagger').forEach(p=>
  gsap.from(p.children,{y:50,opacity:0,duration:0.85,stagger:0.15,ease:'power2.out',
    scrollTrigger:{trigger:p,start:'top 85%'}}));
gsap.utils.toArray('.reveal-left').forEach(el=>
  gsap.from(el,{x:-70,opacity:0,duration:1.0,ease:'power2.out',
    scrollTrigger:{trigger:el,start:'top 85%'}}));
gsap.utils.toArray('.reveal-right').forEach(el=>
  gsap.from(el,{x:70,opacity:0,duration:1.0,ease:'power2.out',
    scrollTrigger:{trigger:el,start:'top 85%'}}));
document.querySelectorAll('[data-count]').forEach(el=>{
  const end=+el.dataset.count,suf=el.dataset.suf||'';
  gsap.from({v:0},{v:end,duration:2.5,ease:'power1.out',
    scrollTrigger:{trigger:el,start:'top 80%'},
    onUpdate(){el.textContent=Math.round(this.targets()[0].v).toLocaleString()+suf}});
});
const nav=document.querySelector('.navbar');
if(nav)ScrollTrigger.create({start:'top -80',
  onUpdate:s=>nav.classList.toggle('scrolled',s.progress>0)});
</script>

USE THIS CSS TEMPLATE EXACTLY (fill --accent, --font-d, --font-b for the subject):
<style>
:root{
  --bg:#0A0A10;--bg2:#13131E;--card:#0E0E1A;
  --border:rgba(255,255,255,0.07);
  --accent:#FILL;--accent2:#FILL;--accent-rgb:R,G,B;
  --text:#F0F0FF;--muted:rgba(220,220,240,0.60);
  --font-d:'Display',sans-serif;--font-b:'Body',sans-serif;
  --radius:16px;--radius-lg:28px;
  --glow:0 0 40px rgba(var(--accent-rgb),.45);
  --shadow:0 8px 40px rgba(0,0,0,.4);
}
/* LIGHT THEME override: --bg:#FAFAFA;--text:#111118;--muted:rgba(20,20,40,.6);--card:#FFF;--border:rgba(0,0,0,.08); */
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0;}
html{scroll-behavior:smooth;}
body{background:var(--bg);color:var(--text);font-family:var(--font-b);overflow-x:hidden;line-height:1.6;}
img{max-width:100%;height:auto;display:block;}
.gradient-text{background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}
.glass{background:rgba(255,255,255,0.04);backdrop-filter:blur(16px) saturate(180%);border:1px solid var(--border);border-radius:var(--radius-lg);}
.btn{display:inline-flex;align-items:center;gap:10px;text-decoration:none;font-weight:700;border-radius:50px;transition:all .25s;cursor:pointer;}
.btn-primary{background:var(--accent);color:#000;padding:16px 38px;border:none;}
.btn-primary:hover{transform:translateY(-3px);box-shadow:var(--glow);}
.btn-ghost{border:2px solid var(--accent);color:var(--accent);padding:14px 36px;background:transparent;}
.btn-ghost:hover{background:var(--accent);color:#000;}
.navbar{position:fixed;top:0;left:0;right:0;z-index:1000;height:68px;display:flex;align-items:center;justify-content:space-between;padding:0 5vw;backdrop-filter:blur(20px) saturate(200%);background:rgba(10,10,16,.82);border-bottom:1px solid var(--border);transition:box-shadow .3s;}
.navbar.scrolled{box-shadow:0 4px 30px rgba(0,0,0,.5);}
.nav-logo{font-family:var(--font-d);font-weight:900;font-size:1.4rem;color:var(--text);text-decoration:none;}
.nav-links{display:flex;gap:2rem;list-style:none;}
.nav-links a{color:var(--muted);text-decoration:none;transition:color .2s;font-weight:500;}
.nav-links a:hover{color:var(--accent);}
.hero{min-height:100vh;display:flex;align-items:center;position:relative;overflow:hidden;padding:100px 5vw 80px;}
.hero-content{max-width:680px;position:relative;z-index:2;}
.hero-title{font-family:var(--font-d);font-size:clamp(3rem,8vw,7rem);font-weight:900;line-height:1.0;letter-spacing:-0.04em;margin-bottom:1.5rem;color:#FFFFFF;}
.hero-sub{font-size:clamp(1.1rem,2.5vw,1.35rem);color:rgba(255,255,255,.85);max-width:560px;line-height:1.7;margin-bottom:2.5rem;}
.hero-cta{display:flex;gap:1rem;flex-wrap:wrap;align-items:center;margin-bottom:2rem;}
section{padding:clamp(60px,10vw,120px) 5vw;}
.container{max-width:1200px;margin:0 auto;}
.section-label{font-size:.8rem;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);margin-bottom:1rem;}
.section-title{font-family:var(--font-d);font-size:clamp(2rem,5vw,3.5rem);font-weight:800;margin-bottom:1.5rem;line-height:1.1;color:var(--text);}
.section-sub{font-size:1.1rem;color:var(--muted);max-width:600px;line-height:1.7;margin-bottom:3rem;}
.grid-3{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:2rem;}
.card{background:var(--card);border-radius:var(--radius);border:1px solid var(--border);overflow:hidden;transition:transform .3s,box-shadow .3s;}
.card:hover{transform:translateY(-8px);box-shadow:var(--shadow);}
.card-img{width:100%;height:220px;object-fit:cover;}
.card-body{padding:1.5rem;}
.card-icon{font-size:2.2rem;color:var(--accent);margin-bottom:1rem;}
.card-title{font-weight:700;font-size:1.2rem;margin-bottom:.5rem;color:var(--text);}
.card-text{color:var(--muted);line-height:1.6;}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:2rem;text-align:center;}
.stat-number{font-family:var(--font-d);font-size:clamp(2.5rem,6vw,4rem);font-weight:900;color:var(--accent);}
.stat-label{color:var(--muted);font-size:.95rem;margin-top:.5rem;}
.testimonial-card{background:var(--card);border:1px solid var(--border);border-radius:var(--radius-lg);padding:2rem;}
.testimonial-text{font-size:1.05rem;color:var(--text);line-height:1.7;font-style:italic;margin-bottom:1.5rem;}
.testimonial-author{display:flex;align-items:center;gap:1rem;}
.testimonial-avatar{width:52px;height:52px;border-radius:50%;object-fit:cover;border:2px solid var(--accent);}
.testimonial-name{font-weight:700;color:var(--text);}
.testimonial-meta{color:var(--muted);font-size:.85rem;}
footer{background:var(--bg2);border-top:1px solid var(--border);padding:clamp(50px,8vw,90px) 5vw 30px;}
.footer-grid{display:grid;grid-template-columns:2fr 1fr 1fr;gap:3rem;margin-bottom:3rem;}
.footer-brand{font-family:var(--font-d);font-size:1.5rem;font-weight:900;color:var(--text);}
.footer-tagline{color:var(--muted);margin:.75rem 0 1.5rem;}
.footer-socials{display:flex;gap:.75rem;}
.footer-socials a{width:40px;height:40px;border-radius:50%;background:var(--card);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;color:var(--muted);text-decoration:none;transition:all .2s;}
.footer-socials a:hover{border-color:var(--accent);color:var(--accent);}
.footer-heading{font-weight:700;color:var(--text);margin-bottom:1.25rem;}
.footer-links{list-style:none;display:flex;flex-direction:column;gap:.6rem;}
.footer-links a{color:var(--muted);text-decoration:none;font-size:.9rem;transition:color .2s;}
.footer-links a:hover{color:var(--accent);}
.footer-bottom{border-top:1px solid var(--border);padding-top:1.5rem;text-align:center;color:var(--muted);font-size:.85rem;}
::-webkit-scrollbar{width:6px;}::-webkit-scrollbar-track{background:var(--bg2);}::-webkit-scrollbar-thumb{background:var(--accent);border-radius:3px;}
@media(max-width:768px){.nav-links{display:none;}.footer-grid{grid-template-columns:1fr;}.hero-cta{flex-direction:column;}}
</style>

COLOR + FONT per subject:
Sports/Fitness: --bg:#080808;--accent:#FAFF00;--accent2:#FF6B00;--accent-rgb:250,255,0; font: Bebas+Neue + Inter
Music/Night:    --bg:#060410;--accent:#9B5DE5;--accent2:#00F5D4;--accent-rgb:155,93,229; font: Space+Grotesk + Lato
Tech/SaaS:      --bg:#080A14;--accent:#4361EE;--accent2:#7209B7;--accent-rgb:67,97,238; font: Space+Grotesk + Lato
Food/Rest:      --bg:#0C0600;--accent:#F4A261;--accent2:#E76F51;--accent-rgb:244,162,97; font: Playfair+Display + Lato
Art/Creative:   --bg:#050505;--accent:#FF6B35;--accent2:#FF006E;--accent-rgb:255,107,53; font: DM+Serif+Display + DM+Sans
Luxury:         --bg:#0A0A08;--accent:#C9A96E;--accent2:#E8C99E;--accent-rgb:201,169,110; font: Cormorant+Garamond + Montserrat
Nature/Wellness:--bg:#FAF7F0;--text:#1A1A12;--muted:rgba(30,30,20,.65);--card:#FFF;--border:rgba(0,0,0,.08);--accent:#5C8A4A;--accent2:#8DB87A;--accent-rgb:92,138,74; font: Plus+Jakarta+Sans
Personal/Blog:  --bg:#FAFAFA;--text:#111118;--muted:rgba(20,20,40,.6);--card:#FFF;--border:rgba(0,0,0,.08);--accent:#2D6AFF;--accent2:#5B8AFF;--accent-rgb:45,106,255; font: Playfair+Display + Inter
Gym:            --bg:#0A0506;--accent:#FF2D55;--accent2:#FF6B35;--accent-rgb:255,45,85; font: Bebas+Neue + Inter

SWIPER TESTIMONIALS (MUST have exactly 3 swiper-slide divs — loop mode requires it):
<div class="swiper"><div class="swiper-wrapper"><div class="swiper-slide"><div class="testimonial-card"><p class="testimonial-text">"Real quote 1."</p><div class="testimonial-author"><img src="https://i.pravatar.cc/52?img=1" class="testimonial-avatar" alt=""><div><div class="testimonial-name">Full Name</div><div class="testimonial-meta">City — specific result</div></div></div></div></div><div class="swiper-slide"><div class="testimonial-card"><p class="testimonial-text">"Real quote 2."</p><div class="testimonial-author"><img src="https://i.pravatar.cc/52?img=2" class="testimonial-avatar" alt=""><div><div class="testimonial-name">Full Name 2</div><div class="testimonial-meta">City — specific result</div></div></div></div></div><div class="swiper-slide"><div class="testimonial-card"><p class="testimonial-text">"Real quote 3."</p><div class="testimonial-author"><img src="https://i.pravatar.cc/52?img=3" class="testimonial-avatar" alt=""><div><div class="testimonial-name">Full Name 3</div><div class="testimonial-meta">City — specific result</div></div></div></div></div></div><div class="swiper-pagination"></div></div>
<script>new Swiper('.swiper',{loop:false,autoplay:{delay:4500,disableOnInteraction:false},pagination:{el:'.swiper-pagination',clickable:true},spaceBetween:24});</script>

ALPINE FAQ:
<div x-data="{a:null}"><div><button @click="a=a===1?null:1" style="background:var(--card);color:var(--text);border:1px solid var(--border);width:100%;padding:1.2rem 1.5rem;text-align:left;border-radius:var(--radius);cursor:pointer;display:flex;justify-content:space-between;">Question? <i class="fas fa-chevron-down"></i></button><div x-show="a===1" x-transition style="padding:1rem 1.5rem;color:var(--muted);">Answer.</div></div></div>

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
    import time as _t
    import requests as _rq
    _GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
    _MODELS   = ["llama-3.3-70b-versatile", "gemma2-9b-it", "llama-3.1-8b-instant"]
    messages  = [{"role": "system", "content": _SYSTEM},
                 {"role": "user",   "content": user_prompt}]
    # Two rounds: if every model is rate-limited, wait and try once more —
    # Groq per-minute limits usually clear within ~20 seconds.
    for attempt in range(2):
        if attempt:
            print("[G.I.L. WEBGEN] All models limited — waiting 20s and retrying…")
            _t.sleep(20)
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

def _offline_site(description: str) -> str:
    """
    No-LLM emergency template: when every AI model is unreachable, GIL still
    delivers a clean, professional one-pager built from the description.
    Uses the same :root CSS-variable structure as generated sites, so the
    injected customizer (colors/fonts/images) works on it too.
    """
    name = re.sub(r"\s+", " ", description).strip().title()[:48] or "My Business"
    return f"""<!--GIL-OFFLINE--><!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name}</title>
<link href="https://fonts.googleapis.com/css2?family=Playfair+Display:wght@600;800&family=Inter:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{{--bg:#0C0A08;--bg2:#141110;--text:#F5F1EA;--muted:rgba(245,241,234,.62);
--card:#191512;--border:rgba(255,255,255,.09);--accent:#F4A261;--accent2:#E76F51;
--accent-rgb:244,162,97;--font-d:'Playfair Display',serif;--font-b:'Inter',sans-serif;}}
*{{margin:0;padding:0;box-sizing:border-box;}}
body{{background:var(--bg);color:var(--text);font-family:var(--font-b);line-height:1.6;}}
h1,h2,h3{{font-family:var(--font-d);}}
.hero{{min-height:88vh;display:flex;align-items:center;justify-content:center;text-align:center;
background:linear-gradient(rgba(0,0,0,.55),rgba(12,10,8,.92)),url('HERO_IMG') center/cover;padding:24px;}}
.hero h1{{font-size:clamp(2.6rem,7vw,5rem);color:var(--text);}}
.hero p{{color:var(--muted);max-width:560px;margin:18px auto 30px;font-size:1.1rem;}}
.btn{{display:inline-block;background:var(--accent);color:#000;padding:15px 40px;border-radius:40px;
text-decoration:none;font-weight:600;}}
section{{max-width:1100px;margin:0 auto;padding:80px 24px;}}
.label{{font-size:.8rem;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--accent);}}
h2{{font-size:clamp(1.8rem,4vw,2.8rem);margin:8px 0 34px;}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:24px;}}
.card{{background:var(--card);border:1px solid var(--border);border-radius:16px;overflow:hidden;}}
.card img{{width:100%;height:210px;object-fit:cover;display:block;}}
.card .pad{{padding:22px;}}
.card h3{{color:var(--accent);margin-bottom:8px;}}
.card p{{color:var(--muted);font-size:.95rem;}}
footer{{border-top:1px solid var(--border);text-align:center;padding:36px 20px;color:var(--muted);font-size:.9rem;}}
</style></head><body>
<header class="hero"><div>
<h1>{name}</h1>
<p>Welcome — we're glad you're here. Everything on this page is a starting point: open the Customize panel to make it yours.</p>
<a class="btn" href="#about">Discover more</a>
</div></header>
<section id="about"><span class="label">About us</span>
<h2>Made for you</h2>
<div class="grid">
<div class="card"><img src="CARD_IMG_1" alt=""><div class="pad"><h3>Our story</h3>
<p>Tell your visitors who you are and what makes you different. Click this text in edit mode and write your own.</p></div></div>
<div class="card"><img src="CARD_IMG_2" alt=""><div class="pad"><h3>What we offer</h3>
<p>Describe your products or services here — the things people come to you for.</p></div></div>
<div class="card"><img src="CARD_IMG_3" alt=""><div class="pad"><h3>Visit us</h3>
<p>Add your address, opening hours and how to get in touch.</p></div></div>
</div></section>
<footer>© {name} — built with G.I.L.</footer>
</body></html>"""


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

_FALLBACK_IMGS = {
    # Picsum is a free, reliable CDN — always returns a real photo, no API key needed
    "HERO_IMG":   "https://picsum.photos/seed/hero/1920/1080",
    "CARD_IMG_1": "https://picsum.photos/seed/card1/900/700",
    "CARD_IMG_2": "https://picsum.photos/seed/card2/800/800",
    "CARD_IMG_3": "https://picsum.photos/seed/card3/800/600",
}

def _inject_images(html: str, images: dict) -> str:
    """Replace HERO_IMG / CARD_IMG_N tokens with real paths (or CDN fallback)."""
    mapping = {
        "HERO_IMG":   images.get("hero.jpg")   or _FALLBACK_IMGS["HERO_IMG"],
        "CARD_IMG_1": images.get("photo1.jpg") or _FALLBACK_IMGS["CARD_IMG_1"],
        "CARD_IMG_2": images.get("photo2.jpg") or _FALLBACK_IMGS["CARD_IMG_2"],
        "CARD_IMG_3": images.get("photo3.jpg") or _FALLBACK_IMGS["CARD_IMG_3"],
    }
    for token, path in mapping.items():
        html = html.replace(token, path)
    return html


def _fix_html(html: str) -> str:
    """
    Post-process the generated HTML to fix common LLM output issues:
    - Wrap each non-library inline <script> block in try-catch so one bad
      script doesn't break the whole page
    - Change Swiper loop:true → loop:false to avoid the 'not enough slides'
      warning when the LLM only generates 1 testimonial
    - Replace any leftover image tokens that weren't in the expected position
    """
    # 1. Fallback: replace any remaining raw tokens (e.g. inside CSS url())
    for token, url in _FALLBACK_IMGS.items():
        html = html.replace(token, url)

    # 2. Swiper: disable loop mode to avoid the 'not enough slides' warning
    html = re.sub(r'\bloop\s*:\s*true\b', 'loop:false', html)

    # 3. Wrap user-written inline scripts in try-catch
    #    Skip CDN script tags (src=...) and the framework init blocks we
    #    already know are correct. Only wrap inline blocks with actual code.
    cdn_hosts = ("cdn.jsdelivr", "cdnjs.", "unpkg.", "gumroad.", "fonts.g")

    def _wrap_script(m: re.Match) -> str:
        attrs = m.group(1)   # everything between <script and >
        body  = m.group(2)   # the script content
        # Don't touch external scripts or empty bodies
        if "src=" in attrs or not body.strip():
            return m.group(0)
        # Don't double-wrap
        if "try{" in body.replace(" ", ""):
            return m.group(0)
        wrapped = f"try{{\n{body}\n}}catch(_e){{console.warn('GIL script error:',_e);}}"
        return f"<script{attrs}>{wrapped}</script>"

    html = re.sub(
        r'<script([^>]*)>([\s\S]*?)</script>',
        _wrap_script,
        html,
        flags=re.IGNORECASE,
    )
    return html


def _build_user_prompt(description: str, images: dict) -> str:
    """Build the LLM user message with image tokens and optional pricing tiers."""
    lines = [f"Build a complete, production-quality website for: {description}\n"]

    # Always use placeholder tokens — they get replaced by _inject_images() after generation
    lines.append(
        "IMAGES — use ONLY these exact token strings (do NOT use loremflickr):\n"
        "  Hero section background: HERO_IMG\n"
        "  Card / section images:   CARD_IMG_1   CARD_IMG_2   CARD_IMG_3\n"
        "Hero CSS: background:linear-gradient(rgba(0,0,0,.62),rgba(0,0,0,.32)),url(HERO_IMG) center/cover no-repeat fixed;\n"
        "Card img: <img src=\"CARD_IMG_1\" alt=\"...\" loading=\"lazy\">\n"
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
    Concurrent pipeline — ONE LLM call, images injected after:
    - Thread A: generate HTML using HERO_IMG/CARD_IMG_N placeholder tokens
    - Thread B: generate 4 real FLUX images concurrently
    After both finish, replace tokens with real paths (instant string replace).
    """
    import concurrent.futures

    img_dir = out_folder / "images"

    try:
        import activity
        aid_html = activity.start("code",  "Designing the page")
        aid_imgs = activity.start("image", "Generating site images")
    except Exception:
        aid_html = aid_imgs = None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        html_future = ex.submit(_generate_html, description, {})
        img_future  = ex.submit(_generate_site_images, description, img_dir)
        html   = html_future.result()
        images = img_future.result()

    try:
        import activity
        if aid_html is not None:
            (activity.fail if html.startswith("ERROR:") else activity.done)(aid_html)
        if aid_imgs is not None:
            activity.done(aid_imgs, f"{len(images)} images ready" if images
                          else "using fallback images")
    except Exception:
        pass

    if html.startswith("ERROR:"):
        # Last line of defense: build the site from the offline template so
        # the user still gets what they asked for even with every LLM down.
        print(f"[G.I.L. WEBGEN] {html} — falling back to offline template")
        try:
            import activity
            activity.instant("code", "AI unavailable — using offline template")
        except Exception:
            pass
        html = _offline_site(description)

    html = _inject_images(html, images)
    if images:
        print(f"[G.I.L. WEBGEN] Injected {len(images)} real images")

    html = _fix_html(html)
    try:
        from web_editor import inject_editor
        html = inject_editor(html)
    except Exception as exc:
        print(f"[G.I.L. WEBGEN] editor injection skipped: {exc}")
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


def generate_for_project(folder_path: str | Path, description: str = None) -> tuple[str, Path | None]:
    """Read TASK.md, generate index.html in that folder, open in browser.
    Returns (message, html_path_or_None)."""
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
                "Add one describing what you want and ask me again.", None)

    html = _run_generation(description, folder)
    if html.startswith("ERROR:"):
        return html[6:].strip(), None

    out = folder / "index.html"
    try:
        out.write_text(html, encoding="utf-8")
    except Exception as exc:
        return f"Website generated but couldn't save to {folder.name} — {exc}.", None

    _open_file(out)
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = m.group(1).strip() if m else folder.name.replace("-", " ").title()
    return _done_message(title, html), out


def generate(utterance: str) -> tuple[str, Path | None]:
    """Generate a website from a voice command and open it in the browser.
    Returns (message, html_path_or_None)."""
    description = _extract_description(utterance)
    if not description:
        return ("What kind of website? For example: 'a coffee shop website' or "
                "'a portfolio for a photographer'.", None)

    slug   = _sanitize(description)
    folder = _OUT_DIR / slug
    folder.mkdir(parents=True, exist_ok=True)

    html = _run_generation(description, folder)
    if html.startswith("ERROR:"):
        return html[6:].strip(), None

    path = folder / "index.html"
    try:
        path.write_text(html, encoding="utf-8")
    except Exception as exc:
        return f"Website generated but couldn't save — {exc}.", None

    _open_file(path)
    m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
    title = m.group(1).strip() if m else description.title()

    return _done_message(title, html), path


def _done_message(title: str, html: str) -> str:
    if "<!--GIL-OFFLINE-->" in html:
        return (f"My AI models are busy right now, so I built '{title}' from my "
                "offline template — it's open in your browser. Use the Customize "
                "panel on the page to add your own text, colors and images.")
    return (f"Done — '{title}' is open in your browser. "
            "Click Customize on the page to make it yours.")
