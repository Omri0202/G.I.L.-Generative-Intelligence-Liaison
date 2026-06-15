"""
studio3d.py — Project G.I.L.
Two rendering modes:
  - OBJECT mode  : holographic wireframe for single objects (knight, robot, etc.)
  - SCENE mode   : solid-material environment scenes (school yard, park, city, etc.)

In SCENE mode, the HTML template pre-defines high-quality helper functions
(makeTree, makeBuilding, makePerson, etc.). The LLM only decides placement.
"""

import os
import re
import sys
import subprocess
import webbrowser
import requests
from datetime import datetime
from pathlib import Path

_DIR = Path(__file__).parent
_RUNNER = _DIR / "_webview_runner.py"
_PYTHONW = Path(sys.executable).parent / "pythonw.exe"
if not _PYTHONW.exists():
    _PYTHONW = Path(sys.executable)

GROQ_URL      = "https://api.groq.com/openai/v1/chat/completions"
GROQ_3D_MODEL = "deepseek-r1-distill-llama-70b"

_VENDOR_DIR = Path(__file__).parent / "data" / "vendor"
_LIBS = {
    "three.min.js":     "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js",
    "OrbitControls.js": "https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js",
}

_SCENE_KEYWORDS = {
    "yard","park","garden","forest","jungle","beach","desert","city","town",
    "street","village","room","office","classroom","hallway","corridor","stadium",
    "arena","market","plaza","square","field","farm","island","mountain","valley",
    "cave","dungeon","castle","campus","playground","school","airport","station",
    "harbour","harbor","space station","battlefield","alley","rooftop","lake",
    "river","canyon","ruins","warehouse","hangar","dojo","arena","colosseum",
}

_HUMANOID_KEYWORDS = {
    "person","human","man","woman","boy","girl","body","figure","character",
    "knight","warrior","soldier","fighter","ninja","samurai","viking","pirate",
    "assassin","archer","guard","hero","villain","wizard","mage","sorcerer",
    "zombie","vampire","werewolf","alien","athlete","dancer","doctor","nurse",
    "astronaut","pilot","detective","spy","cowboy","gladiator","paladin",
    "barbarian","ranger","monk","priest","king","queen","prince","princess",
    "humanoid","android","cyborg","statue","peasant","thief","rogue","cleric",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _strip_thinking(text: str) -> str:
    """Remove <think>...</think> blocks that reasoning models prepend."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.strip()

def _ensure_threejs() -> dict:
    _VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    urls = {}
    for filename, cdn_url in _LIBS.items():
        local = _VENDOR_DIR / filename
        if not local.exists():
            print(f"[G.I.L. STUDIO] Downloading {filename}...")
            r = requests.get(cdn_url, timeout=30)
            r.raise_for_status()
            local.write_bytes(r.content)
        urls[filename] = f"file:///{str(local).replace(chr(92), '/')}"
    return urls


def _open_app_window(html_path: str, title: str = "G.I.L. 3D Studio") -> None:
    """Open the 3D HTML in a frameless native window (pywebview) — no browser chrome."""
    file_url = f"file:///{str(html_path).replace(chr(92), '/')}"

    # Try pywebview via subprocess runner (keeps GIL's main thread free)
    try:
        subprocess.Popen(
            [str(_PYTHONW), str(_RUNNER), file_url, title, "1400", "900"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print(f"[G.I.L. STUDIO] Opened via pywebview runner: {html_path}")
        return
    except Exception as exc:
        print(f"[G.I.L. STUDIO] pywebview runner failed ({exc}), trying Edge --app")

    # Fallback: Edge in app mode (no tabs, no address bar — closest to native)
    for exe in [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        "msedge", "chrome",
    ]:
        try:
            subprocess.Popen([exe, f"--app={file_url}", "--window-size=1400,900"],
                             creationflags=subprocess.CREATE_NO_WINDOW)
            return
        except (FileNotFoundError, OSError):
            continue
    webbrowser.open(file_url)


def _is_scene(description: str) -> bool:
    low = description.lower()
    return any(kw in low for kw in _SCENE_KEYWORDS)


def _is_humanoid(description: str) -> bool:
    low = description.lower()
    return any(kw in low for kw in _HUMANOID_KEYWORDS)


# ══════════════════════════════════════════════════════════════════════════════
# OBJECT MODE  — holographic wireframe
# ══════════════════════════════════════════════════════════════════════════════

_SYS_OBJ = """\
You are an expert Three.js engineer building accurate 3D holographic wireframe models.

FUNCTION: createHoloPart(geometry, colorHex, x, y, z) — use for every part.
  model.add(createHoloPart(new THREE.SomeGeometry(...), 0x00bfff, x, y, z));

RULES:
- First line: const model = new THREE.Group();
- Use ONLY createHoloPart(). Pass 0x00bfff as color for all parts (renderer uses cyan wireframe).
- Do NOT call scene.add(model). Do NOT declare extra variables.
- Return ONLY raw JavaScript. No markdown. No comments.

CRITICAL — ORGANIC WIREFRAME (avoid mechanical-looking cylinder rings):
  PREFER scaled SphereGeometry over CylinderGeometry for organic parts:
    const p=createHoloPart(new THREE.SphereGeometry(1,32,24),0x00bfff,x,y,z);
    p.scale.set(rx, ry, rz);  ← scale to desired ellipsoid dimensions
  SphereGeometry(r, 32, 24)          ← for round parts, always 32×24
  CylinderGeometry(rT, rB, h, 20, 3) ← only for truly cylindrical parts (barrels, poles)
  BoxGeometry(w, h, d, 4, 6, 2)      ← subdivide so mesh grid shows
  TorusGeometry(r, tube, 16, 32)     ← rings/tori

HUMANOID SCALE (standing height ≈ 1.9 units, use this for any person/character):
  Head    SphereGeometry(0.115,32,24)     y=+0.875
  Neck    CylinderGeometry(0.055,0.06,0.13,20,2)  y=+0.745
  Torso   CylinderGeometry(0.19,0.17,0.55,20,4)   y=+0.35
  Hips    CylinderGeometry(0.16,0.14,0.25,20,2)   y=+0.025
  ShoulderL CylinderGeometry(0.05,0.05,0.14,16,2) x=-0.22 y=+0.57 rotation.z=1.57
  ShoulderR same, x=+0.22
  UpperArmL CylinderGeometry(0.055,0.048,0.30,16,3) x=-0.32 y=+0.34
  UpperArmR same, x=+0.32
  ForearmL  CylinderGeometry(0.042,0.035,0.27,16,3) x=-0.34 y=+0.075
  ForearmR  same, x=+0.34
  HandL     SphereGeometry(0.04,16,12)   x=-0.34 y=-0.09
  HandR     same, x=+0.34
  ThighL    CylinderGeometry(0.075,0.065,0.40,20,3) x=-0.09 y=-0.31
  ThighR    same, x=+0.09
  ShinL     CylinderGeometry(0.055,0.042,0.36,20,3) x=-0.09 y=-0.72
  ShinR     same, x=+0.09
  FootL     BoxGeometry(0.10,0.055,0.22,3,2,4)      x=-0.09 y=-0.945 z=+0.04
  FootR     same, x=+0.09

For non-humanoid subjects: use same high segment counts. Build the subject accurately.
Produce 18–30 well-placed parts. Keep everything within ±2.5 units of origin.
"""


_HTML_OBJ = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>G.I.L. 3D · {title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#000812;overflow:hidden;font-family:'Courier New',monospace;color:#00bfff}}
canvas{{display:block;position:fixed;top:0;left:0}}
body::after{{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,10,30,0.15) 3px,rgba(0,10,30,0.15) 4px);pointer-events:none;z-index:99}}
.corner{{position:fixed;width:32px;height:32px;z-index:100;border-color:rgba(0,191,255,0.6);border-style:solid}}
.corner.tl{{top:72px;left:14px;border-width:2px 0 0 2px}}
.corner.tr{{top:72px;right:14px;border-width:2px 2px 0 0}}
.corner.bl{{bottom:14px;left:14px;border-width:0 0 2px 2px}}
.corner.br{{bottom:14px;right:14px;border-width:0 2px 2px 0}}
#topbar{{position:fixed;top:0;left:0;right:0;z-index:100;height:60px;display:flex;align-items:center;justify-content:space-between;padding:0 28px;background:linear-gradient(180deg,rgba(0,8,20,0.97) 0%,transparent 100%);border-bottom:1px solid rgba(0,191,255,0.12)}}
#app-name{{font-size:15px;font-weight:bold;letter-spacing:9px;color:#00bfff;text-shadow:0 0 18px #00bfff99}}
#topbar-right{{font-size:10px;letter-spacing:3px;color:rgba(0,191,255,0.4);text-align:right;line-height:1.7}}
#model-title{{position:fixed;top:68px;left:0;right:0;z-index:100;text-align:center;font-size:17px;font-weight:bold;letter-spacing:6px;text-transform:uppercase;color:#00bfff;text-shadow:0 0 28px #00bfff,0 0 60px rgba(0,191,255,0.4);padding:14px 60px 4px}}
#model-sub{{position:fixed;top:112px;left:0;right:0;z-index:100;text-align:center;font-size:10px;letter-spacing:3px;color:rgba(0,191,255,0.35)}}
#data-panel{{position:fixed;top:50%;right:20px;z-index:100;transform:translateY(-50%);width:160px;border:1px solid rgba(0,191,255,0.2);background:rgba(0,8,20,0.8);padding:14px 16px;font-size:10px;letter-spacing:2px;line-height:2.2}}
#data-panel .row{{display:flex;justify-content:space-between;color:rgba(0,191,255,0.5)}}
#data-panel .val{{color:#00bfff}}
#data-panel h4{{font-size:9px;letter-spacing:4px;color:rgba(0,191,255,0.35);border-bottom:1px solid rgba(0,191,255,0.15);padding-bottom:6px;margin-bottom:8px}}
#radar{{position:fixed;bottom:80px;left:28px;z-index:100;width:72px;height:72px;border-radius:50%;border:1px solid rgba(0,191,255,0.25);background:rgba(0,8,20,0.7);overflow:hidden}}
#radar-inner{{width:100%;height:100%;position:relative;border-radius:50%}}
#radar-inner::before{{content:'';position:absolute;top:50%;left:50%;width:1px;height:50%;background:linear-gradient(to top,rgba(0,191,255,0.8),transparent);transform-origin:bottom center;transform:translateX(-50%);animation:rspin 3s linear infinite}}
#radar-inner::after{{content:'';position:absolute;inset:0;border-radius:50%;background:conic-gradient(from 0deg,rgba(0,191,255,0.15),transparent 60%,transparent);animation:rspin 3s linear infinite}}
@keyframes rspin{{from{{transform:rotate(0deg)}}to{{transform:rotate(360deg)}}}}
#radar-lbl{{position:fixed;bottom:60px;left:28px;z-index:100;font-size:8px;letter-spacing:3px;color:rgba(0,191,255,0.3);width:72px;text-align:center}}
#bottombar{{position:fixed;bottom:0;left:0;right:0;z-index:100;height:36px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:linear-gradient(0deg,rgba(0,8,20,0.95) 0%,transparent 100%);border-top:1px solid rgba(0,191,255,0.1);font-size:9px;letter-spacing:3px;color:rgba(0,191,255,0.35)}}
#ticker{{overflow:hidden;white-space:nowrap;flex:1;margin:0 20px}}
#ticker-inner{{display:inline-block;animation:scroll 22s linear infinite}}
@keyframes scroll{{from{{transform:translateX(100vw)}}to{{transform:translateX(-100%)}}}}
#controls{{position:fixed;bottom:44px;left:50%;transform:translateX(-50%);display:flex;gap:10px;z-index:100}}
.btn{{padding:8px 18px;border:1px solid rgba(0,191,255,0.3);background:rgba(0,8,20,0.85);color:rgba(0,191,255,0.7);font-family:'Courier New',monospace;font-size:10px;font-weight:bold;letter-spacing:2px;cursor:pointer;border-radius:3px;transition:all 0.2s}}
.btn:hover{{background:rgba(0,191,255,0.12);color:#00bfff;box-shadow:0 0 16px rgba(0,191,255,0.25)}}
#sdot{{display:inline-block;width:6px;height:6px;border-radius:50%;background:#00ff88;margin-right:6px;animation:pulse 2s ease-in-out infinite;box-shadow:0 0 8px #00ff88}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
#atm{{position:fixed;inset:0;background:radial-gradient(ellipse at 50% 48%,rgba(0,40,110,0.32) 0%,transparent 62%);pointer-events:none;z-index:0;}}
.reticle{{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:340px;height:340px;border-radius:50%;border:1px solid rgba(0,191,255,0.07);pointer-events:none;z-index:2;animation:rpulse 5s ease-in-out infinite;}}
.reticle::after{{content:'';position:absolute;inset:20px;border-radius:50%;border:1px solid rgba(0,191,255,0.04);}}
@keyframes rpulse{{0%,100%{{opacity:0.55;transform:translate(-50%,-50%) scale(1);}}50%{{opacity:1;transform:translate(-50%,-50%) scale(1.06);}}}}
</style>
</head>
<body>
<div id="topbar">
  <span id="app-name">G . I . L .</span>
  <div id="topbar-right">3D ANALYSIS STUDIO<br>{date}</div>
</div>
<div id="model-title">{title}</div>
<div id="model-sub">AI GENERATED &nbsp;·&nbsp; G.I.L. 3D ENGINE &nbsp;·&nbsp; REAL-TIME RENDER</div>
<div class="corner tl"></div><div class="corner tr"></div>
<div class="corner bl"></div><div class="corner br"></div>
<div id="atm"></div><div class="reticle"></div>
<div id="data-panel">
  <h4>ANALYSIS DATA</h4>
  <div class="row"><span>STATUS</span><span class="val"><span id="sdot"></span>LIVE</span></div>
  <div class="row"><span>ENGINE</span><span class="val">GROQ 70B</span></div>
  <div class="row"><span>FPS</span><span class="val" id="hfps">60</span></div>
  <div class="row"><span>DEPTH</span><span class="val" id="hdepth">6.00</span></div>
  <div class="row"><span>ROT X</span><span class="val" id="hrx">0.0</span></div>
  <div class="row"><span>ROT Y</span><span class="val" id="hry">0.0</span></div>
  <div class="row"><span>PARTS</span><span class="val" id="hparts">0</span></div>
</div>
<div id="radar"><div id="radar-inner"></div></div>
<div id="radar-lbl">TRACKING</div>
<div id="controls">
  <button class="btn" onclick="dlPNG()">SAVE PNG</button>
  <button class="btn" onclick="togWire()">WIREFRAME</button>
  <button class="btn" onclick="togSpin()">PAUSE</button>
  <button class="btn" onclick="window.close()">CLOSE</button>
</div>
<div id="bottombar">
  <span><span id="sdot"></span>SYSTEM ONLINE</span>
  <div id="ticker"><span id="ticker-inner">G.I.L. GENERATIVE INTELLIGENCE LIAISON &nbsp;·&nbsp; 3D ANALYSIS MODULE &nbsp;·&nbsp; HOLOGRAPHIC RENDER ENGINE &nbsp;·&nbsp; ALL SYSTEMS NOMINAL &nbsp;·&nbsp;</span></div>
  <span>THREE.JS r128</span>
</div>
<script src="{three_url}"></script>
<script src="{orbit_url}"></script>
<script>
(function(){{
'use strict';
const renderer=new THREE.WebGLRenderer({{antialias:true,preserveDrawingBuffer:true}});
renderer.setSize(innerWidth,innerHeight);renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.shadowMap.enabled=true;renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.4;
document.body.appendChild(renderer.domElement);
const bloom=document.createElement('canvas');bloom.style.cssText='position:fixed;top:0;left:0;pointer-events:none;z-index:1;mix-blend-mode:screen;filter:blur(11px) brightness(2.0);opacity:0.46;';document.body.appendChild(bloom);const bctx=bloom.getContext('2d');function sizeBloom(){{bloom.width=renderer.domElement.width;bloom.height=renderer.domElement.height;bloom.style.width=innerWidth+'px';bloom.style.height=innerHeight+'px';}}sizeBloom();window.addEventListener('resize',sizeBloom);
const scene=new THREE.Scene();scene.background=new THREE.Color(0x000812);scene.fog=new THREE.FogExp2(0x000812,0.022);
const camera=new THREE.PerspectiveCamera(48,innerWidth/innerHeight,0.01,500);camera.position.set(0,1.8,6.5);
const controls=new THREE.OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.dampingFactor=0.06;controls.autoRotate=true;controls.autoRotateSpeed=1.2;
controls.minDistance=2;controls.maxDistance=22;
scene.add(new THREE.AmbientLight(0x001a3a,3));
const key=new THREE.DirectionalLight(0x2255ff,4);key.position.set(4,8,5);key.castShadow=true;key.shadow.mapSize.setScalar(2048);scene.add(key);
const fillL=new THREE.PointLight(0x00bfff,6,25);fillL.position.set(-5,3,-4);scene.add(fillL);
const rimL=new THREE.PointLight(0x0033ff,4,18);rimL.position.set(0,-4,-6);scene.add(rimL);
const topL=new THREE.PointLight(0x44aaff,3,14);topL.position.set(0,9,0);scene.add(topL);
const accentL=new THREE.PointLight(0x00ffaa,2,10);accentL.position.set(3,-1,4);scene.add(accentL);
const grid=new THREE.GridHelper(24,48,0x061428,0x030a14);grid.position.y=-2.8;scene.add(grid);
const ground=new THREE.Mesh(new THREE.PlaneGeometry(24,24),new THREE.MeshStandardMaterial({{color:0x010510,metalness:0.9,roughness:0.1,transparent:true,opacity:0.55}}));
ground.rotation.x=-Math.PI/2;ground.position.y=-2.81;ground.receiveShadow=true;scene.add(ground);
const sGeo=new THREE.BufferGeometry();const sN=1600,sPos=new Float32Array(sN*3);
for(let i=0;i<sN;i++){{sPos[i*3]=(Math.random()-.5)*120;sPos[i*3+1]=(Math.random()-.5)*120;sPos[i*3+2]=(Math.random()-.5)*120;}}
sGeo.setAttribute('position',new THREE.BufferAttribute(sPos,3));
scene.add(new THREE.Points(sGeo,new THREE.PointsMaterial({{color:0x223366,size:0.08,transparent:true,opacity:0.8}})));
function createHoloPart(geo,col,x,y,z){{
  const g=new THREE.Group();
  const wf=new THREE.LineSegments(new THREE.WireframeGeometry(geo),new THREE.LineBasicMaterial({{color:0x00bfff,transparent:true,opacity:0.52,blending:THREE.AdditiveBlending,depthWrite:false}}));
  g.add(wf);
  const inner=new THREE.Mesh(geo,new THREE.MeshBasicMaterial({{color:0x003d66,transparent:true,opacity:0.13,depthWrite:false,side:THREE.DoubleSide}}));g.add(inner);
  if(x!==undefined)g.position.set(x,y,z);return g;
}}
function buildHuman(){{
  const g=new THREE.Group();
  const S=(r,w,h)=>new THREE.SphereGeometry(r,w||32,h||24);
  function add(geo,x,y,z,sx,sy,sz){{
    const p=createHoloPart(geo,0x00bfff,x,y,z);
    if(sx!==undefined)p.scale.set(sx,sy!==undefined?sy:sx,sz!==undefined?sz:sx);
    g.add(p);return p;
  }}
  // Head — perfect sphere
  add(S(0.12),0,0.885,0);
  // Neck — narrow ellipsoid
  add(S(1,18,12),0,0.75,0, 0.054,0.075,0.054);
  // Torso — LatheGeometry: organic chest-waist-shoulder silhouette
  const tp=[new THREE.Vector2(0.13,0),new THREE.Vector2(0.148,0.08),new THREE.Vector2(0.172,0.2),new THREE.Vector2(0.183,0.34),new THREE.Vector2(0.188,0.48),new THREE.Vector2(0.168,0.56)];
  add(new THREE.LatheGeometry(tp,24),0,0.19,0);
  // Pelvis — LatheGeometry: hip flare
  const pp=[new THREE.Vector2(0.148,0),new THREE.Vector2(0.168,0.1),new THREE.Vector2(0.162,0.21),new THREE.Vector2(0.118,0.27)];
  add(new THREE.LatheGeometry(pp,24),0,-0.11,0);
  // Shoulder caps — squashed spheres bridging torso to arms
  add(S(1,18,14),-0.27,0.565,0, 0.092,0.085,0.076);
  add(S(1,18,14), 0.27,0.565,0, 0.092,0.085,0.076);
  // Upper arms — elongated ellipsoids (look like biceps, not tin cans)
  add(S(1,20,16),-0.335,0.345,0, 0.068,0.19,0.062);
  add(S(1,20,16), 0.335,0.345,0, 0.068,0.19,0.062);
  // Forearms — tapered ellipsoids
  add(S(1,20,16),-0.355,0.055,0, 0.054,0.165,0.048);
  add(S(1,20,16), 0.355,0.055,0, 0.054,0.165,0.048);
  // Hands — wide flat ellipsoids
  add(S(1,18,14),-0.355,-0.145,0, 0.062,0.062,0.038);
  add(S(1,18,14), 0.355,-0.145,0, 0.062,0.062,0.038);
  // Thighs — wide organic ellipsoids
  add(S(1,22,18),-0.1,-0.315,0, 0.095,0.24,0.092);
  add(S(1,22,18), 0.1,-0.315,0, 0.095,0.24,0.092);
  // Calves — tapered ellipsoids
  add(S(1,22,18),-0.1,-0.74,0, 0.074,0.22,0.068);
  add(S(1,22,18), 0.1,-0.74,0, 0.074,0.22,0.068);
  // Feet — subdivided box
  add(new THREE.BoxGeometry(0.1,0.055,0.22,4,2,6),-0.1,-0.962,0.038);
  add(new THREE.BoxGeometry(0.1,0.055,0.22,4,2,6), 0.1,-0.962,0.038);
  return g;
}}
const rings=[];
[{{r:1.7,tube:0.022,col:0x00bfff,rx:Math.PI/2,ry:0,spd:-0.55}},{{r:2.2,tube:0.016,col:0x0088ff,rx:Math.PI/3,ry:0.5,spd:0.40}},{{r:2.7,tube:0.012,col:0x00ffbb,rx:Math.PI/6,ry:1.1,spd:-0.28}},{{r:3.1,tube:0.009,col:0x0055cc,rx:Math.PI/4,ry:-0.6,spd:0.20}},{{r:3.6,tube:0.007,col:0x00bfff,rx:0.3,ry:0.3,spd:-0.14}},{{r:4.1,tube:0.005,col:0x003388,rx:Math.PI/5,ry:2.0,spd:0.09}}].forEach(d=>{{
  const r=new THREE.Mesh(new THREE.TorusGeometry(d.r,d.tube,8,120),new THREE.MeshBasicMaterial({{color:d.col,transparent:true,opacity:0.55,blending:THREE.AdditiveBlending,depthWrite:false}}));
  r.rotation.x=d.rx;r.rotation.y=d.ry;r._spd=d.spd;scene.add(r);rings.push(r);
}});
const scanMat=new THREE.MeshBasicMaterial({{color:0x00bfff,transparent:true,opacity:0.07,side:THREE.DoubleSide,blending:THREE.AdditiveBlending,depthWrite:false}});
const scanBeam=new THREE.Mesh(new THREE.PlaneGeometry(8,0.04),scanMat);scene.add(scanBeam);
const scanLine=new THREE.Line((()=>{{const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.BufferAttribute(new Float32Array([-4,0,0,4,0,0]),3));return g;}})(),new THREE.LineBasicMaterial({{color:0x00ffff,transparent:true,opacity:0.5}}));scene.add(scanLine);
let model=null;
try{{
{model_code}
if(model){{model.traverse(c=>{{if(c.isMesh||c.isLine)c.castShadow=true;}});if(!scene.children.includes(model))scene.add(model);}}
}}catch(err){{console.error('[GIL]',err);model=new THREE.Group();model.add(createHoloPart(new THREE.IcosahedronGeometry(1,2),0x00bfff,0,0,0));scene.add(model);}}
let pc=0;if(model)model.traverse(c=>{{if(c.isMesh)pc++;}});document.getElementById('hparts').textContent=pc;
window.addEventListener('resize',()=>{{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);}});
let t=0,fc=0,lft=performance.now(),spinning=true,wireOn=false;
function animate(){{requestAnimationFrame(animate);t+=0.008;fc++;
  fillL.intensity=5.5+Math.sin(t*1.1)*1.2;rimL.intensity=3.5+Math.sin(t*0.7)*0.8;accentL.intensity=1.8+Math.sin(t*1.5)*0.8;
  rings.forEach(r=>{{r.rotation.z+=r._spd*0.01;r.material.opacity=0.52+Math.sin(t*0.85+r._spd)*0.16;}});
  const sy=Math.sin(t*0.55)*2.2;scanBeam.position.y=sy;scanLine.position.y=sy;
  if(model){{model.position.y=Math.sin(t*0.4)*0.12;model.traverse(c=>{{if(c.isLineSegments)c.material.opacity=0.44+Math.sin(t*1.3)*0.18;}});}}
  controls.update();renderer.render(scene,camera);
  bctx.clearRect(0,0,bloom.width,bloom.height);bctx.drawImage(renderer.domElement,0,0);
  const now=performance.now();if(now-lft>800){{const fps=Math.round(fc*1000/(now-lft));fc=0;lft=now;
    document.getElementById('hfps').textContent=fps;document.getElementById('hdepth').textContent=camera.position.length().toFixed(2);
    document.getElementById('hrx').textContent=(camera.rotation.x*57.3).toFixed(1);document.getElementById('hry').textContent=(camera.rotation.y*57.3).toFixed(1);
  }}
}}
animate();
window.togWire=()=>{{wireOn=!wireOn;if(model)model.traverse(c=>{{if(c.isMesh)c.material.wireframe=wireOn;}});}};
window.togSpin=()=>{{spinning=!spinning;controls.autoRotate=spinning;}};
window.dlPNG=()=>{{renderer.render(scene,camera);const a=document.createElement('a');a.download='GIL_3D_{safe_title}_{date}.png';a.href=renderer.domElement.toDataURL('image/png');a.click();}};
}})();
</script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# SCENE MODE  — solid-material environment scenes
# ══════════════════════════════════════════════════════════════════════════════

_SYS_SCENE = """\
You are composing a 3D scene by calling pre-built helper functions.
Your job is PLACEMENT AND COMPOSITION only — not building geometry from scratch.

SCENE BOUNDS: x: -8 to +8, z: -8 to +6. Ground is at y=0.

AVAILABLE HELPERS (call as many times as needed):

makeTree(x, z, height=2.5, trunkCol=0x7B5E3C, leavesCol=0x2E7D32)
makeBuilding(x, z, w, h, d, wallCol=0xD4724A, roofCol=0x8B4513, label="")
makePerson(x, z, shirtCol=0x1565C0, pantsCol=0x424242, skinCol=0xFFCC80)
makeBench(x, z, rotY=0)
makeHoop(x, z)
makeSwingSet(x, z)
makeSlide(x, z, rotY=0)
makeFence(x1, z1, x2, z2, col=0xB0BEC5)
makeCar(x, z, rotY=0, col=0xE53935)
makeStreetLight(x, z)
makeRock(x, z, size=0.5, col=0x888888)
makeBall(x, z, r=0.15, col=0xFF5722)
makeFlowerPatch(x, z, col=0xFF8A80)
makePoolTable(x, z)
makeDumpster(x, z, col=0x4CAF50)
makeFlagpole(x, z, flagCol=0xF44336)

RULES:
- Do NOT declare model — it already exists.
- Call helper functions to build the scene
- Spread elements realistically — no clustering at center
- Use 20-40 helper calls for a rich, populated scene
- Do NOT define variables, Do NOT use createHoloPart, Do NOT use raw THREE meshes
- Do NOT call scene.add(model)
- Return ONLY the JavaScript helper function calls
"""


_SYS_ACCESSORIES = """\
A humanoid body wireframe already exists as `model` (a THREE.Group).
Your job: add weapons, armor, clothing, props ON TOP of the body.

FUNCTION: model.add(createHoloPart(geometry, 0x00bfff, x, y, z));
  For rotation: const p=createHoloPart(geo,0x00bfff,x,y,z); p.rotation.z=N; model.add(p);

BODY REFERENCE (positions to attach accessories):
  Head: y=+0.875  | Helmet/crown: y=+0.96
  Neck: y=+0.745  | Shoulders: y=+0.575 x=±0.225
  Back: x=0 y=+0.36 z=+0.18  | Belt/waist: y=0.03
  Hands: y=-0.10 x=±0.34     | Feet: y=-0.95 x=±0.09
  Held weapon (right hand): x=+0.34 y=-0.10 to +0.50, z=0

GEOMETRY (high segments for smooth look):
  CylinderGeometry(rT,rB,h,16,3) | BoxGeometry(w,h,d,3,4,2)
  SphereGeometry(r,24,18) | TorusGeometry(r,t,12,24) | ConeGeometry(r,h,16,3)

RULES:
- Do NOT redeclare model. Do NOT call scene.add(model).
- Add 6-14 parts appropriate to the subject description.
- Return ONLY raw JavaScript model.add() calls.
"""


_HTML_SCENE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>G.I.L. 3D SCENE · {title}</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#0a0e1a;overflow:hidden;font-family:'Courier New',monospace;color:#00bfff}}
canvas{{display:block;position:fixed;top:0;left:0}}
body::after{{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 3px,rgba(0,10,30,0.1) 3px,rgba(0,10,30,0.1) 4px);pointer-events:none;z-index:99}}
.corner{{position:fixed;width:32px;height:32px;z-index:100;border-color:rgba(0,191,255,0.55);border-style:solid}}
.corner.tl{{top:72px;left:14px;border-width:2px 0 0 2px}}
.corner.tr{{top:72px;right:14px;border-width:2px 2px 0 0}}
.corner.bl{{bottom:14px;left:14px;border-width:0 0 2px 2px}}
.corner.br{{bottom:14px;right:14px;border-width:0 2px 2px 0}}
#topbar{{position:fixed;top:0;left:0;right:0;z-index:100;height:60px;display:flex;align-items:center;justify-content:space-between;padding:0 28px;background:linear-gradient(180deg,rgba(5,10,25,0.97) 0%,transparent 100%);border-bottom:1px solid rgba(0,191,255,0.1)}}
#app-name{{font-size:15px;font-weight:bold;letter-spacing:9px;color:#00bfff;text-shadow:0 0 18px #00bfff99}}
#topbar-right{{font-size:10px;letter-spacing:3px;color:rgba(0,191,255,0.4);text-align:right;line-height:1.7}}
#scene-title{{position:fixed;top:68px;left:0;right:0;z-index:100;text-align:center;font-size:17px;font-weight:bold;letter-spacing:6px;text-transform:uppercase;color:#00bfff;text-shadow:0 0 28px #00bfff,0 0 60px rgba(0,191,255,0.35);padding:14px 60px 4px}}
#scene-sub{{position:fixed;top:112px;left:0;right:0;z-index:100;text-align:center;font-size:10px;letter-spacing:3px;color:rgba(0,191,255,0.35)}}
#data-panel{{position:fixed;top:50%;right:20px;z-index:100;transform:translateY(-50%);width:160px;border:1px solid rgba(0,191,255,0.2);background:rgba(5,10,25,0.85);padding:14px 16px;font-size:10px;letter-spacing:2px;line-height:2.2}}
#data-panel .row{{display:flex;justify-content:space-between;color:rgba(0,191,255,0.5)}}
#data-panel .val{{color:#00bfff}}
#data-panel h4{{font-size:9px;letter-spacing:4px;color:rgba(0,191,255,0.35);border-bottom:1px solid rgba(0,191,255,0.15);padding-bottom:6px;margin-bottom:8px}}
#controls{{position:fixed;bottom:44px;left:50%;transform:translateX(-50%);display:flex;gap:10px;z-index:100}}
.btn{{padding:8px 18px;border:1px solid rgba(0,191,255,0.3);background:rgba(5,10,25,0.85);color:rgba(0,191,255,0.7);font-family:'Courier New',monospace;font-size:10px;font-weight:bold;letter-spacing:2px;cursor:pointer;border-radius:3px;transition:all 0.2s}}
.btn:hover{{background:rgba(0,191,255,0.12);color:#00bfff;box-shadow:0 0 16px rgba(0,191,255,0.25)}}
#bottombar{{position:fixed;bottom:0;left:0;right:0;z-index:100;height:36px;display:flex;align-items:center;justify-content:space-between;padding:0 24px;background:linear-gradient(0deg,rgba(5,10,25,0.95) 0%,transparent 100%);border-top:1px solid rgba(0,191,255,0.1);font-size:9px;letter-spacing:3px;color:rgba(0,191,255,0.35)}}
#ticker{{overflow:hidden;white-space:nowrap;flex:1;margin:0 20px}}
#ticker-inner{{display:inline-block;animation:scroll 25s linear infinite}}
@keyframes scroll{{from{{transform:translateX(100vw)}}to{{transform:translateX(-100%)}}}}
#sdot{{display:inline-block;width:6px;height:6px;border-radius:50%;background:#00ff88;margin-right:6px;animation:pulse 2s ease-in-out infinite;box-shadow:0 0 8px #00ff88}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:0.3}}}}
</style>
</head>
<body>
<div id="topbar">
  <span id="app-name">G . I . L .</span>
  <div id="topbar-right">3D SCENE STUDIO<br>{date}</div>
</div>
<div id="scene-title">{title}</div>
<div id="scene-sub">AI SCENE COMPOSITION &nbsp;·&nbsp; G.I.L. 3D ENGINE &nbsp;·&nbsp; REAL-TIME RENDER</div>
<div class="corner tl"></div><div class="corner tr"></div>
<div class="corner bl"></div><div class="corner br"></div>
<div id="data-panel">
  <h4>SCENE DATA</h4>
  <div class="row"><span>STATUS</span><span class="val"><span id="sdot"></span>LIVE</span></div>
  <div class="row"><span>MODE</span><span class="val">SCENE</span></div>
  <div class="row"><span>FPS</span><span class="val" id="hfps">60</span></div>
  <div class="row"><span>DEPTH</span><span class="val" id="hdepth">18.0</span></div>
  <div class="row"><span>OBJECTS</span><span class="val" id="hobjs">0</span></div>
  <div class="row"><span>MESHES</span><span class="val" id="hmesh">0</span></div>
</div>
<div id="controls">
  <button class="btn" onclick="dlPNG()">SAVE PNG</button>
  <button class="btn" onclick="togSpin()">PAUSE</button>
  <button class="btn" onclick="window.close()">CLOSE</button>
</div>
<div id="bottombar">
  <span><span id="sdot"></span>SCENE ACTIVE</span>
  <div id="ticker"><span id="ticker-inner">G.I.L. GENERATIVE INTELLIGENCE LIAISON &nbsp;·&nbsp; 3D SCENE MODULE &nbsp;·&nbsp; ENVIRONMENT RENDER ENGINE &nbsp;·&nbsp; ALL SYSTEMS NOMINAL &nbsp;·&nbsp;</span></div>
  <span>THREE.JS r128</span>
</div>
<script src="{three_url}"></script>
<script src="{orbit_url}"></script>
<script>
(function(){{
'use strict';
const renderer=new THREE.WebGLRenderer({{antialias:true,preserveDrawingBuffer:true}});
renderer.setSize(innerWidth,innerHeight);renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.shadowMap.enabled=true;renderer.shadowMap.type=THREE.PCFSoftShadowMap;
renderer.toneMapping=THREE.ACESFilmicToneMapping;renderer.toneMappingExposure=1.1;
document.body.appendChild(renderer.domElement);
const bloom=document.createElement('canvas');bloom.style.cssText='position:fixed;top:0;left:0;pointer-events:none;z-index:1;mix-blend-mode:screen;filter:blur(8px) brightness(1.5);opacity:0.32;';document.body.appendChild(bloom);const bctx=bloom.getContext('2d');function sizeBloom(){{bloom.width=renderer.domElement.width;bloom.height=renderer.domElement.height;bloom.style.width=innerWidth+'px';bloom.style.height=innerHeight+'px';}}sizeBloom();window.addEventListener('resize',sizeBloom);
const scene=new THREE.Scene();scene.background=new THREE.Color(0x0a0e1a);scene.fog=new THREE.Fog(0x0a0e1a,28,55);
const camera=new THREE.PerspectiveCamera(55,innerWidth/innerHeight,0.1,500);camera.position.set(0,10,18);camera.lookAt(0,0,0);
const controls=new THREE.OrbitControls(camera,renderer.domElement);
controls.enableDamping=true;controls.dampingFactor=0.05;controls.autoRotate=true;controls.autoRotateSpeed=0.5;
controls.target.set(0,1,0);controls.minDistance=4;controls.maxDistance=40;

// Lighting — natural daylight feel
const sun=new THREE.DirectionalLight(0xfff5e0,2.8);sun.position.set(8,16,6);sun.castShadow=true;
sun.shadow.mapSize.width=sun.shadow.mapSize.height=2048;sun.shadow.camera.near=0.5;sun.shadow.camera.far=60;
sun.shadow.camera.left=-15;sun.shadow.camera.right=15;sun.shadow.camera.top=15;sun.shadow.camera.bottom=-15;
scene.add(sun);
scene.add(new THREE.AmbientLight(0x8899cc,1.2));
const skyL=new THREE.HemisphereLight(0xaaccff,0x334422,0.8);scene.add(skyL);

// Ground — large grass plane
const groundGeo=new THREE.PlaneGeometry(28,24);
const groundMat=new THREE.MeshLambertMaterial({{color:0x5a8a3c}});
const ground=new THREE.Mesh(groundGeo,groundMat);ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;scene.add(ground);
// Concrete/asphalt area in center
const paveGeo=new THREE.PlaneGeometry(14,12);
const paveMat=new THREE.MeshLambertMaterial({{color:0x8a8a82}});
const pave=new THREE.Mesh(paveGeo,paveMat);pave.rotation.x=-Math.PI/2;pave.position.y=0.01;scene.add(pave);

// ── Scene helper functions ──────────────────────────────────────────────────
function mat(col){{return new THREE.MeshLambertMaterial({{color:col}});}}
function addMesh(geo,col,x,y,z,rx,ry,rz){{
  const m=new THREE.Mesh(geo,mat(col));m.position.set(x,y,z);
  if(rx)m.rotation.x=rx;if(ry)m.rotation.y=ry;if(rz)m.rotation.z=rz;
  m.castShadow=true;m.receiveShadow=true;model.add(m);return m;
}}

function makeTree(x,z,h=2.5,tc=0x7B5E3C,lc=0x2E7D32){{
  addMesh(new THREE.CylinderGeometry(0.1,0.16,h*0.38,8),tc,x,h*0.19,z);
  addMesh(new THREE.ConeGeometry(h*0.42,h*0.75,8),lc,x,h*0.55+h*0.375,z);
  addMesh(new THREE.ConeGeometry(h*0.32,h*0.58,8),0x388E3C,x,h*0.72+h*0.375,z);
}}

function makeBuilding(x,z,w,h,d,wc=0xD4724A,rc=0x8B4513){{
  addMesh(new THREE.BoxGeometry(w,h,d),wc,x,h/2,z);
  addMesh(new THREE.BoxGeometry(w+0.3,0.22,d+0.3),rc,x,h+0.11,z);
  const wr=Math.max(1,Math.floor(w/1.8));
  for(let i=0;i<wr;i++){{const wx=x-w/2+w/(wr+1)*(i+1);addMesh(new THREE.BoxGeometry(0.7,0.7,0.08),0x90CAF9,wx,h*0.62,z-d/2-0.04);}}
  addMesh(new THREE.BoxGeometry(0.6,1.0,0.08),0x5D4037,x,0.5,z-d/2-0.04);
}}

function makePerson(x,z,sc=0x1565C0,pc=0x424242,sk=0xFFCC80){{
  addMesh(new THREE.SphereGeometry(0.11,8,8),sk,x,0.88,z);
  addMesh(new THREE.CylinderGeometry(0.085,0.095,0.38,6),sc,x,0.57,z);
  addMesh(new THREE.CylinderGeometry(0.055,0.055,0.32,6),sc,x-0.13,0.5,z,0,0,0.25);
  addMesh(new THREE.CylinderGeometry(0.055,0.055,0.32,6),sc,x+0.13,0.5,z,0,0,-0.25);
  addMesh(new THREE.CylinderGeometry(0.065,0.065,0.36,6),pc,x-0.07,0.19,z);
  addMesh(new THREE.CylinderGeometry(0.065,0.065,0.36,6),pc,x+0.07,0.19,z);
}}

function makeBench(x,z,ry=0){{
  const g=new THREE.Group();
  [new THREE.Mesh(new THREE.BoxGeometry(1.1,0.07,0.34),mat(0x8B5E3C)),new THREE.Mesh(new THREE.BoxGeometry(1.1,0.34,0.06),mat(0x8B5E3C))].forEach((m,i)=>{{m.position.set(0,0.45+i*0.2,i?0.14:0);g.add(m);}});
  [[-0.42,0.2],[-0.42,-0.2],[0.42,0.2],[0.42,-0.2]].forEach(([lx,lz])=>{{const l=new THREE.Mesh(new THREE.BoxGeometry(0.05,0.44,0.05),mat(0x607D8B));l.position.set(lx,0.22,lz);g.add(l);}});
  g.position.set(x,0,z);g.rotation.y=ry;model.add(g);
}}

function makeHoop(x,z){{
  addMesh(new THREE.CylinderGeometry(0.045,0.045,3.6,8),0x607D8B,x,1.8,z);
  addMesh(new THREE.BoxGeometry(0.75,0.05,0.045),0x607D8B,x+0.38,3.3,z);
  addMesh(new THREE.BoxGeometry(0.52,0.42,0.025),0xF5F5F5,x+0.75,3.05,z);
  const h=new THREE.Mesh(new THREE.TorusGeometry(0.24,0.016,8,32),mat(0xFF6F00));h.rotation.x=Math.PI/2;h.position.set(x+0.75,2.82,z);model.add(h);
}}

function makeSwingSet(x,z){{
  addMesh(new THREE.CylinderGeometry(0.04,0.04,3.4,6),0x9E9E9E,x-1.2,1.7,z,0,0,0.15);
  addMesh(new THREE.CylinderGeometry(0.04,0.04,3.4,6),0x9E9E9E,x+1.2,1.7,z,0,0,-0.15);
  addMesh(new THREE.CylinderGeometry(0.04,0.04,2.9,6),0x9E9E9E,x,1.7,z,Math.PI/2,0,0);
  addMesh(new THREE.BoxGeometry(0.4,0.05,0.2),0x8D6E63,x-0.5,0.8,z);
  addMesh(new THREE.BoxGeometry(0.4,0.05,0.2),0x8D6E63,x+0.5,0.8,z);
}}

function makeSlide(x,z,ry=0){{
  const g=new THREE.Group();
  const sl=new THREE.Mesh(new THREE.BoxGeometry(0.7,0.06,2.4),mat(0xE53935));sl.position.set(0,1.3,0.2);sl.rotation.x=-0.55;g.add(sl);
  const plt=new THREE.Mesh(new THREE.BoxGeometry(0.8,0.06,0.8),mat(0xFF8A80));plt.position.set(0,2.2,-0.8);g.add(plt);
  [[0.35,1.1,0.85],[-0.35,1.1,0.85]].forEach(([lx,ly,lz])=>{{const l=new THREE.Mesh(new THREE.CylinderGeometry(0.04,0.04,ly*2,6),mat(0xBDBDBD));l.position.set(lx,ly,lz);g.add(l);}});
  g.position.set(x,0,z);g.rotation.y=ry;model.add(g);
}}

function makeFence(x1,z1,x2,z2,col=0xB0BEC5){{
  const dx=x2-x1,dz=z2-z1,len=Math.sqrt(dx*dx+dz*dz),angle=Math.atan2(dx,dz),mx=(x1+x2)/2,mz=(z1+z2)/2;
  addMesh(new THREE.BoxGeometry(0.04,0.04,len),col,mx,1.05,mz,0,angle,0);
  addMesh(new THREE.BoxGeometry(0.04,0.04,len),col,mx,0.65,mz,0,angle,0);
  const n=Math.max(2,Math.floor(len/1.1));
  for(let i=0;i<=n;i++){{const t=i/n;addMesh(new THREE.BoxGeometry(0.06,1.2,0.06),col,x1+dx*t,0.6,z1+dz*t);}}
}}

function makeCar(x,z,ry=0,col=0xE53935){{
  const g=new THREE.Group();
  const body=new THREE.Mesh(new THREE.BoxGeometry(2.0,0.55,0.9),mat(col));body.position.y=0.45;g.add(body);
  const top=new THREE.Mesh(new THREE.BoxGeometry(1.2,0.42,0.82),mat(col));top.position.set(-0.1,0.95,0);g.add(top);
  const wgeo=new THREE.CylinderGeometry(0.25,0.25,0.12,12);const wmat=mat(0x212121);
  [[-0.7,0.25,0.48],[-0.7,0.25,-0.48],[0.6,0.25,0.48],[0.6,0.25,-0.48]].forEach(([wx,wy,wz])=>{{const w=new THREE.Mesh(wgeo,wmat);w.rotation.z=Math.PI/2;w.position.set(wx,wy,wz);g.add(w);}});
  g.position.set(x,0,z);g.rotation.y=ry;model.add(g);
}}

function makeStreetLight(x,z){{
  addMesh(new THREE.CylinderGeometry(0.04,0.06,4.5,6),0x9E9E9E,x,2.25,z);
  addMesh(new THREE.CylinderGeometry(0.02,0.02,1.2,6),0x9E9E9E,x+0.6,4.5,z,0,0,-Math.PI/6);
  addMesh(new THREE.SphereGeometry(0.12,8,8),0xFFF9C4,x+1.1,4.3,z);
}}

function makeRock(x,z,sz=0.5,col=0x888888){{
  addMesh(new THREE.DodecahedronGeometry(sz),col,x,sz*0.6,z);
}}

function makeBall(x,z,r=0.15,col=0xFF5722){{
  addMesh(new THREE.SphereGeometry(r,10,10),col,x,r,z);
}}

function makeFlowerPatch(x,z,col=0xFF8A80){{
  for(let i=0;i<5;i++){{const ox=(Math.random()-.5)*0.8,oz=(Math.random()-.5)*0.8;addMesh(new THREE.CylinderGeometry(0.02,0.02,0.3,4),0x4CAF50,x+ox,0.15,z+oz);addMesh(new THREE.SphereGeometry(0.07,6,6),col,x+ox,0.33,z+oz);}}
}}

function makeDumpster(x,z,col=0x4CAF50){{
  addMesh(new THREE.BoxGeometry(1.1,0.8,0.55),col,x,0.4,z);
  addMesh(new THREE.BoxGeometry(1.15,0.06,0.6),0x388E3C,x,0.83,z);
}}

function makeFlagpole(x,z,fc=0xF44336){{
  addMesh(new THREE.CylinderGeometry(0.03,0.04,5,6),0xBDBDBD,x,2.5,z);
  addMesh(new THREE.BoxGeometry(0.7,0.4,0.02),fc,x+0.35,4.7,z);
}}

function makePoolTable(x,z){{
  addMesh(new THREE.BoxGeometry(2.0,0.1,1.1),0x1B5E20,x,0.78,z);
  addMesh(new THREE.BoxGeometry(2.1,0.55,1.2),0x5D4037,x,0.5,z);
}}

// ── AI-placed scene elements ────────────────────────────────────────────────
const model=new THREE.Group();
try{{
// LLM placement:
{scene_code}
}}catch(err){{console.error('[GIL SCENE]',err);}}
model.traverse(c=>{{if(c.isMesh){{c.castShadow=true;c.receiveShadow=true;}}}});
scene.add(model);

let mc=0,oc=0;model.traverse(c=>{{if(c.isMesh)mc++;if(c.isGroup||c.isMesh)oc++;}});
document.getElementById('hmesh').textContent=mc;document.getElementById('hobjs').textContent=oc;

window.addEventListener('resize',()=>{{camera.aspect=innerWidth/innerHeight;camera.updateProjectionMatrix();renderer.setSize(innerWidth,innerHeight);}});
let t=0,fc2=0,lft2=performance.now(),spinning=true;
function animate(){{requestAnimationFrame(animate);t+=0.005;fc2++;controls.update();renderer.render(scene,camera);
  bctx.clearRect(0,0,bloom.width,bloom.height);bctx.drawImage(renderer.domElement,0,0);
  const now=performance.now();if(now-lft2>800){{const fps=Math.round(fc2*1000/(now-lft2));fc2=0;lft2=now;
    document.getElementById('hfps').textContent=fps;document.getElementById('hdepth').textContent=camera.position.length().toFixed(1);
  }}
}}
animate();
window.togSpin=()=>{{spinning=!spinning;controls.autoRotate=spinning;}};
window.dlPNG=()=>{{renderer.render(scene,camera);const a=document.createElement('a');a.download='GIL_SCENE_{safe_title}_{date}.png';a.href=renderer.domElement.toDataURL('image/png');a.click();}};
}})();
</script></body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Groq generation
# ══════════════════════════════════════════════════════════════════════════════

def _call_groq_object(description: str) -> str:
    keys = [k for k in [os.getenv("GROQ_API_KEY",""), os.getenv("GROQ_API_KEY_2","")] if k]
    if not keys:
        return _fallback_object()

    prompt = (
        f"Build an accurate Three.js 3D model of: {description}\n\n"
        "IMPORTANT — WIREFRAME QUALITY:\n"
        "Every geometry MUST use high segment counts so the wireframe grid looks smooth and professional:\n"
        "  SphereGeometry(r, 32, 24)          — never use less than 32×24\n"
        "  CylinderGeometry(rT, rB, h, 20, 3) — 20 radial + 3 height\n"
        "  BoxGeometry(w, h, d, 4, 6, 2)      — subdivide so grid is visible\n"
        "  ConeGeometry(r, h, 20, 3)\n\n"
        "If this is a humanoid: follow the HUMANOID SCALE exactly — 18+ parts.\n"
        "Pass 0x00bfff as color to all createHoloPart() calls.\n"
        "For rotation: const p=createHoloPart(...); p.rotation.z=N; model.add(p);\n"
        "Return ONLY raw JavaScript. No markdown. No comments."
    )
    payload = {"model": GROQ_3D_MODEL,
               "messages": [{"role":"system","content":_SYS_OBJ},{"role":"user","content":prompt}],
               "temperature": 0.6, "max_tokens": 8000}
    for key in keys:
        try:
            r = requests.post(GROQ_URL, json=payload, timeout=45,
                              headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
            if r.status_code == 200:
                code = _strip_thinking(r.json()["choices"][0]["message"]["content"])
                code = re.sub(r"```[a-z]*", "", code).replace("```","").strip()
                code = "\n".join(l for l in code.splitlines() if "scene.add(model)" not in l)
                print(f"[G.I.L. STUDIO] Object: {len(code.splitlines())} lines")
                return code
            print(f"[G.I.L. STUDIO] {r.status_code}")
        except Exception as exc:
            print(f"[G.I.L. STUDIO] {exc}")
    return _fallback_object()


def _call_groq_scene(description: str) -> str:
    keys = [k for k in [os.getenv("GROQ_API_KEY",""), os.getenv("GROQ_API_KEY_2","")] if k]
    if not keys:
        return _fallback_scene()

    prompt = (
        f"Compose a realistic 3D scene of: {description}\n\n"
        "Use the helper functions to place elements across the full scene area.\n"
        "Think about what makes this scene realistic:\n"
        "- What buildings/structures exist?\n"
        "- What vegetation?\n"
        "- What people, vehicles, or objects?\n"
        "- What boundary elements (fences, walls)?\n"
        "Place 20-40 elements spread across x: -8 to +8, z: -8 to +6.\n"
        "Do NOT declare const model — it already exists. Just call the helper functions.\n"
        "Return ONLY the JavaScript helper function calls. No variable declarations."
    )
    payload = {"model": GROQ_3D_MODEL,
               "messages": [{"role":"system","content":_SYS_SCENE},{"role":"user","content":prompt}],
               "temperature": 0.6, "max_tokens": 8000}
    for key in keys:
        try:
            r = requests.post(GROQ_URL, json=payload, timeout=45,
                              headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
            if r.status_code == 200:
                code = _strip_thinking(r.json()["choices"][0]["message"]["content"])
                code = re.sub(r"```[a-z]*", "", code).replace("```","").strip()
                code = "\n".join(l for l in code.splitlines() if "scene.add(model)" not in l)
                print(f"[G.I.L. STUDIO] Scene: {len(code.splitlines())} lines")
                return code
            print(f"[G.I.L. STUDIO] {r.status_code}")
        except Exception as exc:
            print(f"[G.I.L. STUDIO] {exc}")
    return _fallback_scene()


def _call_groq_accessories(description: str) -> str:
    keys = [k for k in [os.getenv("GROQ_API_KEY",""), os.getenv("GROQ_API_KEY_2","")] if k]
    if not keys:
        return ""
    prompt = (
        f"Add equipment, armor, weapons, clothing, and props for: {description}\n\n"
        "The humanoid body wireframe already exists as `model`. Do NOT redeclare it.\n"
        "Add 6-14 accessories using model.add(createHoloPart(...)) calls.\n"
        "Use the body reference positions. High segment counts on all geometry.\n"
        "Return ONLY raw JavaScript model.add() calls."
    )
    payload = {"model": GROQ_3D_MODEL,
               "messages": [{"role":"system","content":_SYS_ACCESSORIES},{"role":"user","content":prompt}],
               "temperature": 0.6, "max_tokens": 4000}
    for key in keys:
        try:
            r = requests.post(GROQ_URL, json=payload, timeout=45,
                              headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"})
            if r.status_code == 200:
                code = _strip_thinking(r.json()["choices"][0]["message"]["content"])
                code = re.sub(r"```[a-z]*", "", code).replace("```","").strip()
                code = "\n".join(l for l in code.splitlines()
                                 if "scene.add(model)" not in l
                                 and "const model" not in l
                                 and "let model" not in l)
                print(f"[G.I.L. STUDIO] Accessories: {len(code.splitlines())} lines")
                return code
        except Exception as exc:
            print(f"[G.I.L. STUDIO] accessories err: {exc}")
    return ""


def _fallback_object() -> str:
    return """\
const model = new THREE.Group();
model.add(createHoloPart(new THREE.IcosahedronGeometry(1.0,2),0x00bfff,0,0,0));
model.add(createHoloPart(new THREE.TorusGeometry(1.4,0.06,8,48),0x0055ff,0,0,0));"""


def _fallback_scene() -> str:
    return """\
makeBuilding(0,-6,8,4,5,0xD4724A,0x8B4513);
makeTree(-6,3);makeTree(-5,-2);makeTree(6,2);makeTree(5,-3);makeTree(-7,-5);
makePerson(2,1);makePerson(-1,2);makePerson(3,-1);
makeBench(0,2);makeBench(4,0,1.57);
makeFlagpole(-5,-6);
makeFence(-8,-8,8,-8);makeFence(-8,-8,-8,6);makeFence(8,-8,8,6);"""


# ══════════════════════════════════════════════════════════════════════════════
# Public API
# ══════════════════════════════════════════════════════════════════════════════

def open_studio(description: str, variant: int = 1, project_name: str = "") -> str:
    print(f"[G.I.L. STUDIO] Generating: {description!r}")
    lib_urls = _ensure_threejs()
    date_str = datetime.now().strftime("%Y-%m-%d")
    safe     = "".join(c if c.isalnum() else "_" for c in description[:24]).strip("_")

    is_env    = _is_scene(description)
    is_human  = not is_env and _is_humanoid(description)
    mode = "SCENE" if is_env else ("HUMANOID" if is_human else "OBJECT")
    print(f"[G.I.L. STUDIO] Mode: {mode}")

    if is_env:
        code = _call_groq_scene(description)
        html = _HTML_SCENE.format(
            title      = description.upper(),
            scene_code = code,
            date       = date_str,
            safe_title = safe,
            three_url  = lib_urls.get("three.min.js",""),
            orbit_url  = lib_urls.get("OrbitControls.js",""),
        )
    elif is_human:
        accessories = _call_groq_accessories(description)
        code = "const model = buildHuman();\n" + accessories
        html = _HTML_OBJ.format(
            title      = description.upper(),
            model_code = code,
            date       = date_str,
            safe_title = safe,
            three_url  = lib_urls.get("three.min.js",""),
            orbit_url  = lib_urls.get("OrbitControls.js",""),
        )
    else:
        code = _call_groq_object(description)
        html = _HTML_OBJ.format(
            title      = description.upper(),
            model_code = code,
            date       = date_str,
            safe_title = safe,
            three_url  = lib_urls.get("three.min.js",""),
            orbit_url  = lib_urls.get("OrbitControls.js",""),
        )

    data_dir  = Path(__file__).parent / "data" / "3d_projects"
    data_dir.mkdir(parents=True, exist_ok=True)
    html_path = data_dir / f"{safe}_{date_str}_v1.html"
    html_path.write_text(html, encoding="utf-8")

    try:
        from learning_projects import add_resource
        add_resource(project_name.strip() or description.title(), "3d_studio",
                     str(html_path), f"3D Studio: {description}")
    except Exception:
        pass

    win_title = f"G.I.L. 3D  ·  {description[:40].upper()}"
    _open_app_window(str(html_path), win_title)
    print(f"[G.I.L. STUDIO] Saved & opened: {html_path}")
    return str(html_path)


def open_studio_variants(description: str, project_name: str = "") -> None:
    open_studio(description, project_name=project_name)


def reopen_studio(html_path: str) -> None:
    p = Path(html_path)
    if p.exists():
        _open_app_window(str(p))
    else:
        print(f"[G.I.L. STUDIO] File not found: {html_path}")
