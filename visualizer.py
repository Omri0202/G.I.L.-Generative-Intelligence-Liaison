"""
visualizer.py — Project G.I.L.
Generates beautiful interactive 3D visualizations for geometry and math.
Opens in the default browser using Three.js — no extra dependencies.
"""

import os
import webbrowser
import tempfile

# ── Shape keyword mapping ──────────────────────────────────────────────────────

_SHAPE_KEYWORDS: dict[str, str] = {
    "sphere":      "sphere",
    "ball":        "sphere",
    "globe":       "sphere",
    "cube":        "cube",
    "box":         "cube",
    "rectangular prism": "box",
    "cuboid":      "box",
    "cylinder":    "cylinder",
    "tube":        "cylinder",
    "cone":        "cone",
    "pyramid":     "pyramid",
    "square pyramid": "pyramid",
    "tetrahedron": "tetrahedron",
    "torus":       "torus",
    "donut":       "torus",
    "ring":        "torus",
    "prism":       "prism",
    "triangular prism": "prism",
    "plane":       "plane",
    "rectangle":   "plane",
    "octahedron":  "octahedron",
}

_GEOMETRY_TRIGGERS = {
    "show", "draw", "display", "visualize", "what does", "what is a",
    "explain", "3d", "model", "shape", "geometry",
}


def detect_shape(text: str) -> str | None:
    lower = text.lower()
    for keyword, shape in _SHAPE_KEYWORDS.items():
        if keyword in lower:
            return shape
    return None


def should_visualize(text: str) -> bool:
    lower = text.lower()
    has_trigger = any(t in lower for t in _GEOMETRY_TRIGGERS)
    has_shape   = detect_shape(text) is not None
    return has_trigger and has_shape


def show_shape(shape: str, problem_text: str = "") -> None:
    """Generate and open a Three.js 3D visualization in the browser."""
    html = _generate_html(shape, problem_text)
    with tempfile.NamedTemporaryFile(
        suffix=".html", delete=False, mode="w", encoding="utf-8"
    ) as f:
        f.write(html)
        path = f.name
    webbrowser.open(f"file:///{path.replace(chr(92), '/')}")


# ── HTML generator ─────────────────────────────────────────────────────────────

def _generate_html(shape: str, problem_text: str = "") -> str:
    shape_js   = _shape_js(shape)
    title      = shape.replace("_", " ").title()
    formula    = _formulas(shape)
    problem_el = f'<div id="problem">{problem_text}</div>' if problem_text else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>G.I.L. — {title}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Rajdhani:wght@400;600&family=Inter:wght@300;400&display=swap');
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    background: radial-gradient(ellipse at center, #0a0d1a 0%, #05060f 100%);
    overflow: hidden;
    font-family: 'Inter', sans-serif;
    color: #fff;
  }}
  canvas {{ display:block; }}

  #hud {{
    position: fixed;
    top: 0; left: 0; right: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    pointer-events: none;
    z-index: 10;
  }}
  #title {{
    margin-top: 28px;
    font-family: 'Rajdhani', sans-serif;
    font-size: 28px;
    font-weight: 600;
    letter-spacing: 6px;
    text-transform: uppercase;
    color: #00bfff;
    text-shadow: 0 0 30px #00bfff, 0 0 60px #0066ff44;
  }}
  #subtitle {{
    margin-top: 6px;
    font-size: 12px;
    letter-spacing: 3px;
    color: #ffffff44;
    text-transform: uppercase;
  }}
  #formula {{
    margin-top: 14px;
    background: rgba(0,191,255,0.07);
    border: 1px solid rgba(0,191,255,0.2);
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 13px;
    color: #00bfffcc;
    letter-spacing: 1px;
    text-align: center;
    backdrop-filter: blur(8px);
  }}
  #problem {{
    margin-top: 10px;
    background: rgba(255,255,255,0.04);
    border: 1px solid rgba(255,255,255,0.1);
    border-radius: 8px;
    padding: 10px 24px;
    font-size: 13px;
    color: #ffffffaa;
    max-width: 600px;
    text-align: center;
  }}

  #hint {{
    position: fixed;
    bottom: 20px;
    left: 50%;
    transform: translateX(-50%);
    font-size: 11px;
    letter-spacing: 2px;
    color: #ffffff22;
    text-transform: uppercase;
    pointer-events: none;
  }}

  #axes-label {{
    position: fixed;
    bottom: 60px;
    right: 30px;
    font-size: 11px;
    color: #ffffff33;
    text-align: right;
    pointer-events: none;
    letter-spacing: 1px;
  }}

  .glow-ring {{
    position: fixed;
    top: 50%; left: 50%;
    transform: translate(-50%, -50%);
    width: 500px; height: 500px;
    border-radius: 50%;
    background: radial-gradient(ellipse, rgba(0,100,255,0.04) 0%, transparent 70%);
    pointer-events: none;
    z-index: 0;
  }}
</style>
</head>
<body>
<div class="glow-ring"></div>

<div id="hud">
  <div id="title">G.I.L. &mdash; {title}</div>
  <div id="subtitle">Interactive 3D Model &nbsp;·&nbsp; Generative Intelligence Liaison</div>
  <div id="formula">{formula}</div>
  {problem_el}
</div>

<div id="hint">Drag to rotate &nbsp;·&nbsp; Scroll to zoom &nbsp;·&nbsp; Right-click to pan</div>
<div id="axes-label">X &nbsp; Y &nbsp; Z</div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/three@0.128.0/examples/js/controls/OrbitControls.js"></script>
<script>
// Scene setup
const scene    = new THREE.Scene();
const camera   = new THREE.PerspectiveCamera(55, innerWidth/innerHeight, 0.1, 1000);
camera.position.set(3, 2, 4);

const renderer = new THREE.WebGLRenderer({{ antialias: true, alpha: true }});
renderer.setSize(innerWidth, innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
document.body.appendChild(renderer.domElement);

// Controls
const controls = new THREE.OrbitControls(camera, renderer.domElement);
controls.enableDamping    = true;
controls.dampingFactor    = 0.06;
controls.autoRotate       = true;
controls.autoRotateSpeed  = 0.8;
controls.minDistance      = 2;
controls.maxDistance      = 12;

// Lighting
const ambientLight = new THREE.AmbientLight(0x0a1a3a, 3);
scene.add(ambientLight);

const keyLight = new THREE.DirectionalLight(0x4488ff, 2);
keyLight.position.set(5, 8, 5);
keyLight.castShadow = true;
scene.add(keyLight);

const fillLight = new THREE.PointLight(0x00bfff, 3, 15);
fillLight.position.set(-4, 2, -4);
scene.add(fillLight);

const rimLight = new THREE.PointLight(0x0033ff, 2, 10);
rimLight.position.set(0, -3, -5);
scene.add(rimLight);

// Ground grid
const gridHelper = new THREE.GridHelper(12, 24, 0x112255, 0x0a1133);
gridHelper.position.y = -2;
scene.add(gridHelper);

// Fog
scene.fog = new THREE.FogExp2(0x05060f, 0.04);

// Materials
const solidMat = new THREE.MeshPhongMaterial({{
  color:       0x0044aa,
  emissive:    0x001133,
  specular:    0x00bfff,
  shininess:   120,
  transparent: true,
  opacity:     0.82,
}});
const wireMat = new THREE.MeshBasicMaterial({{
  color:       0x00bfff,
  wireframe:   true,
  transparent: true,
  opacity:     0.18,
}});
const edgeMat = new THREE.LineBasicMaterial({{
  color:       0x00bfff,
  transparent: true,
  opacity:     0.55,
}});

// Shape
{shape_js}

// Particle field
const particles = new THREE.BufferGeometry();
const pCount = 800;
const pPos = new Float32Array(pCount * 3);
for (let i = 0; i < pCount; i++) {{
  pPos[i*3]   = (Math.random()-0.5)*30;
  pPos[i*3+1] = (Math.random()-0.5)*30;
  pPos[i*3+2] = (Math.random()-0.5)*30;
}}
particles.setAttribute('position', new THREE.BufferAttribute(pPos, 3));
const pMat = new THREE.PointsMaterial({{ color: 0x224488, size: 0.04, transparent: true, opacity: 0.6 }});
scene.add(new THREE.Points(particles, pMat));

// Resize
window.addEventListener('resize', () => {{
  camera.aspect = innerWidth/innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
}});

// Animate
let t = 0;
function animate() {{
  requestAnimationFrame(animate);
  t += 0.01;
  fillLight.intensity = 2.5 + Math.sin(t) * 0.5;
  controls.update();
  renderer.render(scene, camera);
}}
animate();
</script>
</body>
</html>"""


# ── Per-shape Three.js geometry ────────────────────────────────────────────────

def _shape_js(shape: str) -> str:
    shapes = {
        "sphere": """
const geo  = new THREE.SphereGeometry(1.4, 64, 64);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
scene.add(new THREE.Mesh(new THREE.SphereGeometry(1.42,24,24), wireMat));
""",
        "cube": """
const geo  = new THREE.BoxGeometry(2.2,2.2,2.2);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
const edges = new THREE.EdgesGeometry(geo);
scene.add(new THREE.LineSegments(edges, edgeMat));
""",
        "box": """
const geo  = new THREE.BoxGeometry(2.8,1.8,1.6);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
const edges = new THREE.EdgesGeometry(geo);
scene.add(new THREE.LineSegments(edges, edgeMat));
""",
        "cylinder": """
const geo  = new THREE.CylinderGeometry(1.1,1.1,2.8,64);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
scene.add(new THREE.Mesh(new THREE.CylinderGeometry(1.12,1.12,2.8,32), wireMat));
""",
        "cone": """
const geo  = new THREE.ConeGeometry(1.4,3,64);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
scene.add(new THREE.Mesh(new THREE.ConeGeometry(1.42,3,32), wireMat));
""",
        "pyramid": """
const geo  = new THREE.ConeGeometry(1.6,2.8,4);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
const edges = new THREE.EdgesGeometry(geo);
scene.add(new THREE.LineSegments(edges, edgeMat));
""",
        "tetrahedron": """
const geo  = new THREE.TetrahedronGeometry(1.8);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
const edges = new THREE.EdgesGeometry(geo);
scene.add(new THREE.LineSegments(edges, edgeMat));
""",
        "torus": """
const geo  = new THREE.TorusGeometry(1.3,0.45,32,120);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
scene.add(new THREE.Mesh(new THREE.TorusGeometry(1.3,0.46,16,60), wireMat));
""",
        "prism": """
const shape3 = new THREE.Shape();
shape3.moveTo(0,1.4); shape3.lineTo(1.2,-0.7); shape3.lineTo(-1.2,-0.7); shape3.closePath();
const extSettings = {{ depth:2.4, bevelEnabled:false }};
const geo  = new THREE.ExtrudeGeometry(shape3, extSettings);
geo.center();
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
const edges = new THREE.EdgesGeometry(geo);
scene.add(new THREE.LineSegments(edges, edgeMat));
""",
        "plane": """
const geo  = new THREE.PlaneGeometry(3,2.2,8,6);
const mat2 = solidMat.clone(); mat2.side = THREE.DoubleSide;
const mesh = new THREE.Mesh(geo, mat2); mesh.castShadow=true; scene.add(mesh);
const edges = new THREE.EdgesGeometry(new THREE.PlaneGeometry(3,2.2));
scene.add(new THREE.LineSegments(edges, edgeMat));
""",
        "octahedron": """
const geo  = new THREE.OctahedronGeometry(1.6);
const mesh = new THREE.Mesh(geo, solidMat); mesh.castShadow=true; scene.add(mesh);
const edges = new THREE.EdgesGeometry(geo);
scene.add(new THREE.LineSegments(edges, edgeMat));
""",
    }
    return shapes.get(shape, shapes["sphere"])


def _formulas(shape: str) -> str:
    f = {
        "sphere":      "Surface = 4πr² &nbsp;·&nbsp; Volume = ⁴⁄₃πr³",
        "cube":        "Surface = 6a² &nbsp;·&nbsp; Volume = a³",
        "box":         "Surface = 2(lw+lh+wh) &nbsp;·&nbsp; Volume = lwh",
        "cylinder":    "Surface = 2πr(r+h) &nbsp;·&nbsp; Volume = πr²h",
        "cone":        "Surface = πr(r+l) &nbsp;·&nbsp; Volume = ⅓πr²h",
        "pyramid":     "Volume = ⅓ × Base Area × h",
        "tetrahedron": "Surface = √3·a² &nbsp;·&nbsp; Volume = a³/(6√2)",
        "torus":       "Surface = 4π²Rr &nbsp;·&nbsp; Volume = 2π²Rr²",
        "prism":       "Volume = Base Area × h &nbsp;·&nbsp; Surface = 2B + Ph",
        "plane":       "Area = l × w &nbsp;·&nbsp; Perimeter = 2(l+w)",
        "octahedron":  "Surface = 2√3·a² &nbsp;·&nbsp; Volume = √2/3·a³",
    }
    return f.get(shape, "")
