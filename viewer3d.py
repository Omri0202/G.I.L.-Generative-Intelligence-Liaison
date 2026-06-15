"""
viewer3d.py — Project G.I.L.
JARVIS-level holographic 3D panel.
Neon cyan wireframe · energy particles · scanning beam · multi-pass bloom · 60fps
"""

import math
import time
import random
import tkinter as tk
from PIL import Image, ImageDraw, ImageFilter, ImageTk

# ── Canvas / render dims ───────────────────────────────────────────────────────
PANEL_W  = 580
PANEL_H  = 500
SCALE    = 3          # render at 3× for crisp anti-aliased edges
RW       = PANEL_W * SCALE
RH       = PANEL_H * SCALE
RCX      = RW // 2
RCY      = RH // 2 - 10

BG_RGB   = (2, 2, 8)
CYAN     = (0, 191, 255)
FACE_RGB = (2, 8, 22)

# ── Lighting ────────────────────────────────────────────────────────────────────
_LIGHT = (0.6, 0.9, 0.4)   # directional light (normalised below)
_lm = math.sqrt(sum(x*x for x in _LIGHT))
_LIGHT = tuple(x/_lm for x in _LIGHT)


def _face_brightness(verts, face, ry, rx):
    """Return 0-1 brightness for a face given current rotation."""
    pts = [verts[i] for i in face]
    if len(pts) < 3:
        return 0.5
    # compute face normal after rotation
    def rot(x, y, z):
        x2, y2, z2 = _rot_y(x, y, z, ry)
        return _rot_x(x2, y2, z2, rx)
    a = rot(*pts[0]); b = rot(*pts[1]); c = rot(*pts[2])
    ab = (b[0]-a[0], b[1]-a[1], b[2]-a[2])
    ac = (c[0]-a[0], c[1]-a[1], c[2]-a[2])
    nx = ab[1]*ac[2] - ab[2]*ac[1]
    ny = ab[2]*ac[0] - ab[0]*ac[2]
    nz = ab[0]*ac[1] - ab[1]*ac[0]
    nm = math.sqrt(nx*nx + ny*ny + nz*nz) or 1
    nx, ny, nz = nx/nm, ny/nm, nz/nm
    dot = nx*_LIGHT[0] + ny*_LIGHT[1] + nz*_LIGHT[2]
    return max(0.05, (dot + 1) * 0.5)


# ── Shape data ──────────────────────────────────────────────────────────────────

def _sphere(stacks=14, slices=22):
    v, e = [], []
    for i in range(stacks + 1):
        lat = math.pi * (-0.5 + i / stacks)
        for j in range(slices):
            lon = 2 * math.pi * j / slices
            v.append((math.cos(lat)*math.cos(lon),
                      math.sin(lat),
                      math.cos(lat)*math.sin(lon)))
    for i in range(stacks + 1):
        for j in range(slices):
            a = i*slices+j; b = i*slices+(j+1)%slices
            if a < len(v): e.append((a, b))
    for i in range(stacks):
        for j in range(slices):
            e.append((i*slices+j, (i+1)*slices+j))
    return v, e, [], 165

def _cube(s=1.5):
    h = s/2
    v = [(-h,-h,-h),(h,-h,-h),(h,h,-h),(-h,h,-h),
         (-h,-h,h),(h,-h,h),(h,h,h),(-h,h,h)]
    e = [(0,1),(1,2),(2,3),(3,0),(4,5),(5,6),(6,7),(7,4),
         (0,4),(1,5),(2,6),(3,7)]
    f = [[0,1,2,3],[4,5,6,7],[0,1,5,4],[2,3,7,6],[0,3,7,4],[1,2,6,5]]
    return v, e, f, 140

def _cylinder(seg=30, h=1.7, r=1.0):
    v, e = [], []
    for i in range(seg):
        a = 2*math.pi*i/seg
        v += [(r*math.cos(a),h/2,r*math.sin(a)),(r*math.cos(a),-h/2,r*math.sin(a))]
    for i in range(seg):
        a,b = i*2,i*2+1; na,nb = ((i+1)%seg)*2,((i+1)%seg)*2+1
        e += [(a,na),(b,nb),(a,b)]
    return v, e, [], 125

def _cone(seg=30, h=1.8, r=1.0):
    v = [(0,h/2,0)]
    for i in range(seg):
        a = 2*math.pi*i/seg; v.append((r*math.cos(a),-h/2,r*math.sin(a)))
    e = [(1+i,1+(i+1)%seg) for i in range(seg)]+[(0,1+i) for i in range(seg)]
    return v, e, [], 122

def _pyramid():
    v = [(-1,-1,-1),(1,-1,-1),(1,-1,1),(-1,-1,1),(0,1.5,0)]
    e = [(0,1),(1,2),(2,3),(3,0),(0,4),(1,4),(2,4),(3,4)]
    f = [[0,1,2,3],[0,1,4],[1,2,4],[2,3,4],[3,0,4]]
    return v, e, f, 118

def _tetrahedron():
    s = 1.5
    v = [(0,s,0),(-s,-s/1.5,-s/2),(s,-s/1.5,-s/2),(0,-s/1.5,s)]
    e = [(0,1),(0,2),(0,3),(1,2),(2,3),(3,1)]
    f = [[0,1,2],[0,2,3],[0,3,1],[1,2,3]]
    return v, e, f, 124

def _torus(R=1.0, r=0.38, major=32, minor=18):
    v, e = [], []
    for i in range(major):
        a = 2*math.pi*i/major
        for j in range(minor):
            b = 2*math.pi*j/minor
            v.append(((R+r*math.cos(b))*math.cos(a),
                       r*math.sin(b),
                      (R+r*math.cos(b))*math.sin(a)))
    for i in range(major):
        for j in range(minor):
            a=i*minor+j; b=i*minor+(j+1)%minor; c=((i+1)%major)*minor+j
            e += [(a,b),(a,c)]
    return v, e, [], 138

def _octahedron(s=1.4):
    v = [(0,s,0),(0,-s,0),(s,0,0),(-s,0,0),(0,0,s),(0,0,-s)]
    e = [(0,2),(0,3),(0,4),(0,5),(1,2),(1,3),(1,4),(1,5),
         (2,4),(4,3),(3,5),(5,2)]
    f = [[0,2,4],[0,4,3],[0,3,5],[0,5,2],
         [1,2,4],[1,4,3],[1,3,5],[1,5,2]]
    return v, e, f, 126

def _dna(turns=2.5, ppt=22):
    v, e = [], []
    total = int(turns*ppt); R = 0.85
    for i in range(total):
        t = 2*math.pi*i/ppt; y=(i/total)*2.4-1.2
        v += [(R*math.cos(t),y,R*math.sin(t)),
              (R*math.cos(t+math.pi),y,R*math.sin(t+math.pi))]
    for i in range(total-1):
        e += [(i*2,(i+1)*2),(i*2+1,(i+1)*2+1)]
    for i in range(0,total,3):
        e.append((i*2,i*2+1))
    return v, e, [], 110

def _wave(steps=22, size=1.3):
    v, e = [], []
    for i in range(steps):
        for j in range(steps):
            x=(i/(steps-1)-0.5)*size*2; z=(j/(steps-1)-0.5)*size*2
            y=math.sin(math.pi*x)*math.cos(math.pi*z)*0.55
            v.append((x,y,z))
    for i in range(steps):
        for j in range(steps):
            idx=i*steps+j
            if j+1<steps: e.append((idx,idx+1))
            if i+1<steps: e.append((idx,idx+steps))
    return v, e, [], 118

def _spring(turns=5, ppt=20, R=0.7):
    v, e = [], []
    total = int(turns*ppt)
    for i in range(total):
        t=2*math.pi*i/ppt; y=(i/total)*2.2-1.1
        v.append((R*math.cos(t),y,R*math.sin(t)))
    for i in range(total-1): e.append((i,i+1))
    return v, e, [], 115

def _h2o():
    v = [(0,0,0),(-0.96,-0.75,0),(0.96,-0.75,0)]
    e = [(0,1),(0,2)]
    return v, e, [], 200

# ── NEW SHAPES ─────────────────────────────────────────────────────────────────

def _arc_reactor(rings=5, seg=32):
    """Iron Man ARC reactor — concentric rings + triangular segments."""
    v, e = [], []
    radii = [0.18, 0.38, 0.55, 0.72, 0.90, 1.05]
    for ri, r in enumerate(radii):
        base = len(v)
        for j in range(seg):
            a = 2*math.pi*j/seg
            v.append((r*math.cos(a), 0.0, r*math.sin(a)))
        for j in range(seg):
            e.append((base+j, base+(j+1)%seg))
    # vertical spokes every 60°
    for spoke in range(6):
        a = 2*math.pi*spoke/6
        prev = None
        for ri, r in enumerate(radii):
            idx = len(v)
            v.append((r*math.cos(a), 0.0, r*math.sin(a)))
            if prev is not None:
                e.append((prev, idx))
            prev = idx
    # triangular inner pattern
    tri_r = 0.28
    for k in range(3):
        a0 = 2*math.pi*k/3
        a1 = 2*math.pi*(k+1)/3
        ia = len(v); v.append((tri_r*math.cos(a0), 0.04, tri_r*math.sin(a0)))
        ib = len(v); v.append((tri_r*math.cos(a1), 0.04, tri_r*math.sin(a1)))
        ic = len(v); v.append((0, 0.04, 0))
        e += [(ia, ib), (ib, ic), (ic, ia)]
    return v, e, [], 148

def _neural_network():
    """Neural network graph — layered nodes connected by weighted edges."""
    random.seed(42)
    v, e = [], []
    layers = [4, 6, 6, 4]
    layer_x = [-1.2, -0.4, 0.4, 1.2]
    node_indices = []
    for li, (lx, count) in enumerate(zip(layer_x, layers)):
        layer_nodes = []
        spread = 1.0
        for ni in range(count):
            y = (ni - (count-1)/2) * (2*spread/(count))
            z = random.uniform(-0.15, 0.15)
            layer_nodes.append(len(v))
            v.append((lx, y, z))
        node_indices.append(layer_nodes)
    # connect adjacent layers (random subset for cleanliness)
    for li in range(len(layers)-1):
        src = node_indices[li]; dst = node_indices[li+1]
        for s in src:
            for d in random.sample(dst, min(3, len(dst))):
                e.append((s, d))
    return v, e, [], 130

def _hypercube():
    """4D hypercube (tesseract) projected to 3D."""
    v4 = []
    for i in range(16):
        v4.append(((i>>3)&1, (i>>2)&1, (i>>1)&1, i&1))
    # project: w dimension adds small offset
    def proj4(x,y,z,w):
        s = 1.0/(2.5 - w*0.4)
        return ((x-0.5)*s*1.8, (y-0.5)*s*1.8, (z-0.5)*s*1.8)
    v = [proj4(*p) for p in v4]
    e = []
    for i in range(16):
        for j in range(i+1, 16):
            diff = sum(1 for k in range(4) if v4[i][k] != v4[j][k])
            if diff == 1:
                e.append((i, j))
    return v, e, [], 135

def _spaceship():
    """Sci-fi spaceship silhouette — swept fuselage + delta wings."""
    v = [
        # fuselage
        (0, 0, -1.6),(0, 0.18, -0.4),(0, 0.18, 0.6),(0, 0, 1.0),(0, -0.1, 0.6),(0, -0.1, -0.4),
        # top ridge
        (0, 0.35, 0.0),(0, 0.28, -0.8),(0, 0.28, 0.3),
        # left wing
        (-1.3, -0.05, 0.2),(-0.5, 0.0, -0.3),(-0.5, 0.0, 0.5),
        # right wing
        (1.3, -0.05, 0.2),(0.5, 0.0, -0.3),(0.5, 0.0, 0.5),
        # engine pods
        (-0.6, -0.2, 0.8),(-0.6, 0.05, 0.8),(0.6, -0.2, 0.8),(0.6, 0.05, 0.8),
        # cockpit
        (0, 0.42, -0.55),(0, 0.42, -0.1),
    ]
    e = [
        (0,1),(1,2),(2,3),(3,4),(4,5),(5,0),  # fuselage outline
        (1,6),(6,2),(0,7),(7,6),(6,8),(8,2),  # top ridge
        (9,10),(10,11),(11,9),(0,10),(3,11),  # left wing
        (12,13),(13,14),(14,12),(0,13),(3,14),# right wing
        (2,15),(15,16),(2,17),(17,18),        # engine pods
        (18,19),(19,20),(18,20),              # cockpit
        (9,0),(12,0),(9,3),(12,3),
    ]
    return v, e, [], 132

def _city_grid(size=4, h_var=True):
    """Procedural city grid — buildings on a grid plane."""
    random.seed(7)
    v, e = [], []
    spacing = 0.55
    for gx in range(size):
        for gz in range(size):
            cx = (gx - size/2 + 0.5) * spacing
            cz = (gz - size/2 + 0.5) * spacing
            w = random.uniform(0.08, 0.16)
            d = random.uniform(0.08, 0.16)
            h = random.uniform(0.1, 0.7) if h_var else 0.3
            # box corners
            corners = [
                (cx-w, 0,   cz-d),(cx+w, 0,   cz-d),
                (cx+w, 0,   cz+d),(cx-w, 0,   cz+d),
                (cx-w, h,   cz-d),(cx+w, h,   cz-d),
                (cx+w, h,   cz+d),(cx-w, h,   cz+d),
            ]
            base = len(v)
            v.extend(corners)
            e += [(base+0,base+1),(base+1,base+2),(base+2,base+3),(base+3,base+0),
                  (base+4,base+5),(base+5,base+6),(base+6,base+7),(base+7,base+4),
                  (base+0,base+4),(base+1,base+5),(base+2,base+6),(base+3,base+7)]
    return v, e, [], 110

def _black_hole():
    """Black hole with event horizon sphere + accretion disk torus."""
    v, e = [], []
    # event horizon (small sphere)
    stacks, slices = 8, 16
    for i in range(stacks+1):
        lat = math.pi*(-0.5+i/stacks)
        for j in range(slices):
            lon = 2*math.pi*j/slices
            v.append((0.35*math.cos(lat)*math.cos(lon),
                      0.35*math.sin(lat),
                      0.35*math.cos(lat)*math.sin(lon)))
    for i in range(stacks+1):
        for j in range(slices):
            a=i*slices+j; b=i*slices+(j+1)%slices
            if a<len(v): e.append((a,b))
    for i in range(stacks):
        for j in range(slices):
            e.append((i*slices+j,(i+1)*slices+j))
    # accretion disk torus
    R, r, major, minor = 1.0, 0.22, 36, 12
    base = len(v)
    for i in range(major):
        a=2*math.pi*i/major
        for j in range(minor):
            b=2*math.pi*j/minor
            v.append(((R+r*math.cos(b))*math.cos(a),
                       r*math.sin(b)*0.3,
                      (R+r*math.cos(b))*math.sin(a)))
    for i in range(major):
        for j in range(minor):
            a=base+i*minor+j; b=base+i*minor+(j+1)%minor
            c=base+((i+1)%major)*minor+j
            e+=[(a,b),(a,c)]
    return v, e, [], 128

def _fibonacci_spiral():
    """3D Fibonacci / golden ratio spiral shell."""
    v, e = [], []
    phi = (1 + math.sqrt(5)) / 2
    n = 180
    for i in range(n):
        t = i / n
        r = 0.08 * phi ** (2.5 * t)
        lat = math.acos(1 - 2*(i/n))
        lon = 2*math.pi*i/phi
        v.append((r*math.sin(lat)*math.cos(lon),
                  r*math.cos(lat) - 0.5,
                  r*math.sin(lat)*math.sin(lon)))
    for i in range(n-1):
        e.append((i,i+1))
    return v, e, [], 130

def _diamond_lattice(n=3):
    """Diamond cubic crystal lattice."""
    v, e = [], []
    idx_map = {}
    def add(x,y,z):
        key=(round(x,4),round(y,4),round(z,4))
        if key not in idx_map:
            idx_map[key]=len(v); v.append((x,y,z))
        return idx_map[key]
    a=0.65
    for i in range(n):
        for j in range(n):
            for k in range(n):
                x0,y0,z0=i*a-a,j*a-a,k*a-a
                A=add(x0,y0,z0)
                B=add(x0+a/2,y0+a/2,z0)
                C=add(x0+a/2,y0,z0+a/2)
                D=add(x0,y0+a/2,z0+a/2)
                E=add(x0+a/4,y0+a/4,z0+a/4)
                for node in (A,B,C,D):
                    e.append((E,node))
                for pair in [(A,B),(A,C),(A,D),(B,C),(B,D),(C,D)]:
                    if abs(v[pair[0]][0]-v[pair[1]][0])+abs(v[pair[0]][1]-v[pair[1]][1])+abs(v[pair[0]][2]-v[pair[1]][2]) < a*0.76:
                        e.append(pair)
    return v, e, [], 108

def _iron_man_helmet():
    """Stylised Iron Man helmet wireframe."""
    v = [
        # crown
        (0, 1.3, 0),(-0.5, 1.1, -0.3),(0.5, 1.1, -0.3),
        (-0.6, 1.0, 0.1),(0.6, 1.0, 0.1),(-0.4, 1.1, 0.35),(0.4, 1.1, 0.35),
        # jaw / chin
        (0, 0.45, -0.72),(-0.35, 0.5, -0.6),(0.35, 0.5, -0.6),
        (-0.55, 0.6, -0.3),(0.55, 0.6, -0.3),
        (-0.6, 0.7, 0.0),(0.6, 0.7, 0.0),
        (0, 0.3, 0.55),(-0.35, 0.4, 0.5),(0.35, 0.4, 0.5),
        # eye sockets (parallelogram)
        (-0.42, 0.82, -0.52),(-0.12, 0.82, -0.55),
        (-0.42, 0.72, -0.50),(-0.12, 0.72, -0.52),
        (0.12, 0.82, -0.55),(0.42, 0.82, -0.52),
        (0.12, 0.72, -0.52),(0.42, 0.72, -0.50),
        # mouth grille lines
        (-0.3, 0.5, -0.63),(0.3, 0.5, -0.63),
        (-0.3, 0.44, -0.66),(0.3, 0.44, -0.66),
        # neck
        (-0.28, 0.18, 0.3),(0.28, 0.18, 0.3),
        (-0.28, 0.18, -0.18),(0.28, 0.18, -0.18),
    ]
    e = [
        # crown outline
        (0,1),(0,2),(1,3),(2,4),(3,5),(4,6),(5,6),
        (1,8),(2,9),(8,10),(9,11),(10,12),(11,13),
        (3,12),(4,13),(12,15),(13,16),(15,14),(16,14),
        (8,7),(9,7),(7,14),
        # eye left
        (17,18),(18,20),(20,19),(19,17),
        # eye right
        (21,22),(22,24),(24,23),(23,21),
        # mouth
        (25,26),(27,28),
        # neck
        (29,30),(31,32),(29,31),(30,32),(29,14),(30,14),(31,7),(32,7),
        # cheekbones
        (10,17),(10,19),(11,22),(11,24),
    ]
    return v, e, [], 142

# ── Registry ────────────────────────────────────────────────────────────────────
_SHAPES = {
    "sphere":       (_sphere,       "Sphere",            "S = 4πr²   ·   V = ⁴⁄₃πr³"),
    "cube":         (_cube,         "Cube",              "S = 6a²   ·   V = a³"),
    "cylinder":     (_cylinder,     "Cylinder",          "S = 2πr(r+h)   ·   V = πr²h"),
    "cone":         (_cone,         "Cone",              "S = πr(r+l)   ·   V = ⅓πr²h"),
    "pyramid":      (_pyramid,      "Square Pyramid",    "V = ⅓ × B × h"),
    "tetrahedron":  (_tetrahedron,  "Tetrahedron",       "S = √3·a²"),
    "torus":        (_torus,        "Torus",             "S = 4π²Rr   ·   V = 2π²Rr²"),
    "octahedron":   (_octahedron,   "Octahedron",        "S = 2√3·a²"),
    "dna":          (_dna,          "DNA Double Helix",  "B-form: ~10 bp/turn"),
    "wave":         (_wave,         "3D Sine Surface",   "z = sin(πx)·cos(πy)"),
    "spring":       (_spring,       "Spring / Coil",     "F = −kx"),
    "h2o":          (_h2o,          "H₂O Molecule",      "Bond angle: 104.5°"),
    "arc_reactor":  (_arc_reactor,  "ARC Reactor",       "∞ clean energy"),
    "neural":       (_neural_network,"Neural Network",   "20 nodes · 4 layers"),
    "hypercube":    (_hypercube,    "Tesseract (4D→3D)", "16 vertices · 32 edges"),
    "spaceship":    (_spaceship,    "Starfighter",       "delta-wing fuselage"),
    "city":         (_city_grid,    "City Grid",         "procedural skyline"),
    "blackhole":    (_black_hole,   "Black Hole",        "r_s = 2GM/c²"),
    "fibonacci":    (_fibonacci_spiral,"Fibonacci Shell","φ = 1.618..."),
    "diamond":      (_diamond_lattice,"Diamond Lattice", "FCC + basis"),
    "helmet":       (_iron_man_helmet,"Mark L Helmet",   "Fe · Ti · Au alloy"),
}

_KEYWORD_MAP = {
    "sphere":"sphere","ball":"sphere","globe":"sphere","earth":"sphere","planet":"sphere",
    "cube":"cube","box":"cube","square":"cube",
    "cylinder":"cylinder","tube":"cylinder","barrel":"cylinder",
    "cone":"cone","funnel":"cone",
    "pyramid":"pyramid","pharaoh":"pyramid",
    "tetrahedron":"tetrahedron",
    "torus":"torus","donut":"torus","ring":"torus","donut":"torus",
    "octahedron":"octahedron","diamond shape":"octahedron",
    "dna":"dna","helix":"dna","double helix":"dna","genome":"dna",
    "wave":"wave","sine":"wave","surface":"wave",
    "spring":"spring","coil":"spring","helix spring":"spring",
    "water":"h2o","h2o":"h2o","molecule":"h2o",
    "arc reactor":"arc_reactor","reactor":"arc_reactor","iron man reactor":"arc_reactor",
    "neural":"neural","network":"neural","neurons":"neural","brain network":"neural",
    "hypercube":"hypercube","tesseract":"hypercube","4d cube":"hypercube","fourth dimension":"hypercube",
    "spaceship":"spaceship","starfighter":"spaceship","jet":"spaceship","ship":"spaceship","spacecraft":"spaceship",
    "city":"city","buildings":"city","skyline":"city","skyscrapers":"city","town":"city",
    "black hole":"blackhole","blackhole":"blackhole","singularity":"blackhole","event horizon":"blackhole",
    "fibonacci":"fibonacci","spiral":"fibonacci","shell":"fibonacci","golden ratio":"fibonacci",
    "diamond":"diamond","crystal":"diamond","lattice":"diamond","crystalline":"diamond",
    "helmet":"helmet","iron man":"helmet","mark l":"helmet","iron man helmet":"helmet",
}

_GEO_TRIGGERS = {
    "show","draw","display","visualize","what does","what is",
    "3d","model","shape","geometry","render","generate","create",
}

def detect_shape(text):
    lower = text.lower()
    # longest match wins
    for kw in sorted(_KEYWORD_MAP, key=len, reverse=True):
        if kw in lower:
            return _KEYWORD_MAP[kw]
    return None

def should_visualize(text):
    lower = text.lower()
    return any(t in lower for t in _GEO_TRIGGERS) and detect_shape(text) is not None


# ── 3D math ─────────────────────────────────────────────────────────────────────

def _rot_y(x,y,z,a):
    c,s=math.cos(a),math.sin(a); return x*c+z*s,y,-x*s+z*c

def _rot_x(x,y,z,a):
    c,s=math.cos(a),math.sin(a); return x,y*c-z*s,y*s+z*c

def _proj(x,y,z,sc,fov=800):
    d=fov/(fov+z*sc+300); return RCX+x*sc*d*SCALE, RCY-y*sc*d*SCALE, d


# ── Energy particles ─────────────────────────────────────────────────────────────

class _Particle:
    __slots__ = ('theta','phi','speed','radius','phase','size','orbit_tilt')
    def __init__(self):
        self.theta      = random.uniform(0, math.tau)
        self.phi        = random.uniform(0, math.pi)
        self.speed      = random.uniform(0.4, 1.2)
        self.radius     = random.uniform(0.85, 1.35)
        self.phase      = random.uniform(0, math.tau)
        self.size       = random.uniform(2.5, 5.5)
        self.orbit_tilt = random.uniform(0, math.pi)

_PARTICLES = [_Particle() for _ in range(55)]


# ── Stars ────────────────────────────────────────────────────────────────────────

_STARS = [(random.randint(4, PANEL_W-4)*SCALE,
           random.randint(4, PANEL_H-4)*SCALE,
           random.uniform(0.8, 3.0)) for _ in range(200)]


# ── Grid ─────────────────────────────────────────────────────────────────────────

def _grid(size=2.4, step=0.5, y=-1.22):
    lines = []
    vals = [round(-size+i*step, 4) for i in range(int(size*2/step)+2) if -size+i*step <= size+0.01]
    for vv in vals:
        lines += [((-size,y,vv),(size,y,vv)), ((vv,y,-size),(vv,y,size))]
    return lines

_GRID_LINES = _grid()


# ── Main renderer ────────────────────────────────────────────────────────────────

def _draw_frame(verts, edges, faces, scale, ry, rx, t, scan_y):
    """Render one JARVIS-level frame. Returns PIL Image (PANEL_W × PANEL_H)."""

    # ── Background ──────────────────────────────────────────────────────────────
    base = Image.new("RGB", (RW, RH), BG_RGB)
    bd   = ImageDraw.Draw(base)

    # deep space radial glow behind model
    for radius in range(min(RW,RH)//2, 0, -40):
        k = int(radius / (min(RW,RH)/2) * 18)
        bd.ellipse([RCX-radius,RCY-radius,RCX+radius,RCY+radius], fill=(0,k,k*2+4))

    # ── Stars ────────────────────────────────────────────────────────────────────
    for sx, sy, sr in _STARS:
        pulse = 0.3 + 0.7*(0.5+0.5*math.sin(t*0.5+sx*0.009))
        g = int(pulse*60)
        bd.ellipse([sx-sr, sy-sr, sx+sr, sy+sr], fill=(g, g, g+20))

    # ── Scanline ─────────────────────────────────────────────────────────────────
    sl_y = int(RCY + math.sin(t*0.55)*RH*0.42)
    sk   = int(20 + 15*math.sin(t*1.8))
    bd.line([(20,sl_y),(RW-20,sl_y)], fill=(0,sk,sk*2+5), width=SCALE)

    # ── Corner HUD brackets ──────────────────────────────────────────────────────
    bl = 44*SCALE
    for bx,by,dx,dy in [(14,14,1,1),(RW-14,14,-1,1),(14,RH-14,1,-1),(RW-14,RH-14,-1,-1)]:
        bd.line([(bx,by),(bx+dx*bl,by)],  fill=(0,70,110), width=SCALE)
        bd.line([(bx,by),(bx,by+dy*bl)],  fill=(0,70,110), width=SCALE)
        # inner accent
        bd.line([(bx+dx*8,by+dy*8),(bx+dx*22,by+dy*8)], fill=(0,120,180), width=SCALE)
        bd.line([(bx+dx*8,by+dy*8),(bx+dx*8,by+dy*22)], fill=(0,120,180), width=SCALE)

    # ── Project all vertices ──────────────────────────────────────────────────────
    proj = []
    for (x,y,z) in verts:
        x2,y2,z2 = _rot_y(x,y,z,ry)
        x3,y3,z3 = _rot_x(x2,y2,z2,rx)
        sx2,sy2,depth = _proj(x3,y3,z3,scale)
        proj.append((int(sx2),int(sy2),depth,z3))

    # ── Perspective grid ─────────────────────────────────────────────────────────
    gk = int(22+14*math.sin(t*0.8))
    for (ax,ay,az),(bx,by,bz) in _GRID_LINES:
        ax2,ay2,az2=_rot_y(ax,ay,az,ry); ax3,ay3,az3=_rot_x(ax2,ay2,az2,rx)
        bx2,by2,bz2=_rot_y(bx,by,bz,ry); bx3,by3,bz3=_rot_x(bx2,by2,bz2,rx)
        pax,pay,_=_proj(ax3,ay3,az3,scale); pbx,pby,_=_proj(bx3,by3,bz3,scale)
        bd.line([(int(pax),int(pay)),(int(pbx),int(pby))], fill=(0,gk,gk*2+8), width=SCALE)

    # ── Face fill (depth-sorted, phong-shaded) ────────────────────────────────────
    if faces:
        fd = ImageDraw.Draw(base)
        sorted_faces = sorted(faces, key=lambda f: sum(proj[i][3] for i in f)/max(len(f),1))
        for face in sorted_faces:
            pts = [(proj[i][0], proj[i][1]) for i in face]
            brightness = _face_brightness(verts, face, ry, rx)
            r = int(3 + brightness*12)
            g = int(brightness*35)
            b = int(18 + brightness*45)
            fd.polygon(pts, fill=(r, g, b), outline=None)

    # ── GLOW LAYER 1 — wide outer bloom ──────────────────────────────────────────
    glow1 = Image.new("RGBA", (RW, RH), (0,0,0,0))
    g1d   = ImageDraw.Draw(glow1)
    for (a,b) in edges:
        ax,ay,ad,_ = proj[a]; bx2,by2,bd2,_ = proj[b]
        depth = (ad+bd2)*0.5
        alpha = int(min(255, 45 + depth*70))
        g1d.line([(ax,ay),(bx2,by2)], fill=(0,140,210,alpha), width=18*SCALE)
    glow1_blur = glow1.filter(ImageFilter.GaussianBlur(radius=12*SCALE))

    # ── GLOW LAYER 2 — mid bloom ─────────────────────────────────────────────────
    glow2 = Image.new("RGBA", (RW, RH), (0,0,0,0))
    g2d   = ImageDraw.Draw(glow2)
    for (a,b) in edges:
        ax,ay,ad,_ = proj[a]; bx2,by2,bd2,_ = proj[b]
        depth = (ad+bd2)*0.5
        alpha = int(min(255, 90 + depth*100))
        g2d.line([(ax,ay),(bx2,by2)], fill=(0,180,255,alpha), width=7*SCALE)
    glow2_blur = glow2.filter(ImageFilter.GaussianBlur(radius=4*SCALE))

    # ── SHARP CORE EDGES ─────────────────────────────────────────────────────────
    sharp = Image.new("RGBA", (RW, RH), (0,0,0,0))
    shd   = ImageDraw.Draw(sharp)
    for (a,b) in edges:
        ax,ay,ad,_ = proj[a]; bx2,by2,bd2,_ = proj[b]
        depth = (ad+bd2)*0.5
        k = min(1.0, 0.55 + depth*0.4)
        # hot-core colour: near-white centre, deep cyan rim
        r2 = int(k*80); g2 = int(k*210); b2 = int(k*255)
        shd.line([(ax,ay),(bx2,by2)], fill=(r2,g2,b2,235), width=2*SCALE)
        # white hotspot on brightest edges
        if depth > 0.7:
            shd.line([(ax,ay),(bx2,by2)], fill=(255,255,255,80), width=SCALE)

    # ── ENERGY PARTICLES ─────────────────────────────────────────────────────────
    part_layer = Image.new("RGBA", (RW, RH), (0,0,0,0))
    pld        = ImageDraw.Draw(part_layer)
    pscale     = scale * SCALE
    for p in _PARTICLES:
        p.theta += p.speed * 0.022
        # orbit on a great circle tilted by orbit_tilt
        ox = p.radius * math.cos(p.theta)
        oy = p.radius * math.sin(p.theta) * math.cos(p.orbit_tilt)
        oz = p.radius * math.sin(p.theta) * math.sin(p.orbit_tilt)
        # rotate with model
        ox2,oy2,oz2 = _rot_y(ox,oy,oz,ry)
        ox3,oy3,oz3 = _rot_x(ox2,oy2,oz2,rx)
        px,py,pd    = _proj(ox3,oy3,oz3,scale)
        brightness  = 0.3 + 0.7*pd
        pulse       = 0.5 + 0.5*math.sin(t*3+p.phase)
        alpha       = int(min(255, brightness*pulse*220))
        ps          = int(p.size * SCALE * brightness)
        col = (int(50*pulse), int(180*brightness), 255, alpha)
        pld.ellipse([px-ps,py-ps,px+ps,py+ps], fill=col)
        # trail — small dot behind
        trail_theta = p.theta - 0.18
        tx = p.radius*math.cos(trail_theta)
        ty = p.radius*math.sin(trail_theta)*math.cos(p.orbit_tilt)
        tz = p.radius*math.sin(trail_theta)*math.sin(p.orbit_tilt)
        tx2,ty2,tz2 = _rot_y(tx,ty,tz,ry); tx3,ty3,tz3 = _rot_x(tx2,ty2,tz2,rx)
        tpx,tpy,tpd = _proj(tx3,ty3,tz3,scale)
        ta = int(alpha*0.35)
        ts = max(1, int(ps*0.45))
        pld.ellipse([tpx-ts,tpy-ts,tpx+ts,tpy+ts], fill=(0,140,220,ta))
    part_blur = part_layer.filter(ImageFilter.GaussianBlur(radius=2*SCALE))

    # ── SCANNING BEAM ────────────────────────────────────────────────────────────
    scan_layer = Image.new("RGBA", (RW, RH), (0,0,0,0))
    scd        = ImageDraw.Draw(scan_layer)
    sy_scan    = int(scan_y * RH)
    beam_alpha = int(60 + 40*math.sin(t*5))
    for offset in range(-6*SCALE, 6*SCALE, SCALE):
        a = max(0, beam_alpha - abs(offset)//SCALE * 12)
        scd.line([(30, sy_scan+offset),(RW-30, sy_scan+offset)],
                 fill=(80, 220, 255, a), width=SCALE)
    scan_blur = scan_layer.filter(ImageFilter.GaussianBlur(radius=3*SCALE))

    # ── PULSE RINGS ──────────────────────────────────────────────────────────────
    ring_layer = Image.new("RGBA", (RW, RH), (0,0,0,0))
    rld        = ImageDraw.Draw(ring_layer)
    for rn in range(3):
        phase    = t*1.4 + rn*2.1
        ring_r   = int(scale*SCALE*0.78*(1+0.04*math.sin(phase)))
        ring_rh  = int(ring_r*0.26)
        rk       = int(35+25*math.sin(phase))
        rld.ellipse([RCX-ring_r,RCY-ring_rh,RCX+ring_r,RCY+ring_rh],
                    outline=(0,rk,rk*2+25,140+rn*15), width=2*SCALE)
    ring_blur = ring_layer.filter(ImageFilter.GaussianBlur(4*SCALE))

    # ── COMPOSITE ────────────────────────────────────────────────────────────────
    result = base.convert("RGBA")
    result = Image.alpha_composite(result, glow1_blur)
    result = Image.alpha_composite(result, glow2_blur)
    result = Image.alpha_composite(result, sharp)
    result = Image.alpha_composite(result, part_blur)
    result = Image.alpha_composite(result, scan_blur)
    result = Image.alpha_composite(result, ring_blur)

    # ── HUD TELEMETRY ────────────────────────────────────────────────────────────
    hd  = ImageDraw.Draw(result)
    dim = (0,50,80,200)
    # bottom-left
    hd.text((22, RH-52), "G.I.L. · HOLOGRAPHIC DISPLAY",   fill=dim)
    hd.text((22, RH-34), f"VERTS:{len(verts):04d}  EDGES:{len(edges):04d}", fill=dim)
    # bottom-right
    ry_deg = int(math.degrees(ry) % 360)
    rx_deg = int(math.degrees(rx) % 360)
    energy = int(55 + 45*math.sin(t*1.1))
    hd.text((RW-310, RH-52), f"RY:{ry_deg:03d}°  RX:{rx_deg:03d}°", fill=dim)
    hd.text((RW-310, RH-34), f"ENERGY:{energy:03d}%  PARTICLES:{len(_PARTICLES)}", fill=dim)
    # top-right mini data
    hd.text((RW-200, 22), f"FRAME·RATE  60fps", fill=(0,40,60,160))
    hd.text((RW-200, 38), f"RENDER  3× SS",    fill=(0,40,60,160))

    return result.resize((PANEL_W, PANEL_H), Image.LANCZOS).convert("RGB")


# ── GIL 3D Panel ────────────────────────────────────────────────────────────────

class GIL3DPanel(tk.Toplevel):
    def __init__(self, parent, shape="sphere"):
        super().__init__(parent)
        self.overrideredirect(True)
        self.configure(bg="#010108")
        self.geometry(f"{PANEL_W+24}x{PANEL_H+120}")
        self.attributes("-alpha", 0.97)
        self.attributes("-topmost", True)

        self._ry         = 0.0
        self._rx         = 0.22
        self._alive      = True
        self._drag_start = None
        self._win_drag   = None
        self._t0         = time.time()
        self._photo      = None
        self._scan_dir   = 1
        self._scan_y     = 0.15   # 0..1 (fraction of RH)

        self._verts=[]; self._edges=[]; self._faces=[]; self._scale=140
        self._label_str=""; self._formula_str=""

        self._build_ui()
        self.set_shape(shape)
        self._animate()

    # ── UI ────────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        bar = tk.Frame(self, bg="#04040E", height=40)
        bar.pack(fill="x"); bar.pack_propagate(False)
        tk.Label(bar, text="G.I.L.  —  HOLOGRAPHIC  DISPLAY",
                 bg="#04040E", fg="#005577",
                 font=("Courier New", 9, "bold")).pack(side="left", padx=14)
        tk.Button(bar, text="✕", bg="#04040E", fg="#553333",
                  activebackground="#1A0000", activeforeground="#FF5555",
                  font=("Courier New", 13, "bold"), bd=0, relief="flat",
                  command=self._close).pack(side="right", padx=12)
        bar.bind("<ButtonPress-1>", self._win_press)
        bar.bind("<B1-Motion>",     self._win_move)

        self._lbl = tk.Label(self, text="", bg="#010108", fg="#00CFFF",
                             font=("Courier New", 14, "bold"))
        self._lbl.pack(pady=(4,0))

        self.canvas = tk.Canvas(self, width=PANEL_W, height=PANEL_H,
                                bg="#020208", highlightthickness=1,
                                highlightbackground="#002233")
        self.canvas.pack(padx=12, pady=4)
        self.canvas.bind("<ButtonPress-1>", self._mouse_press)
        self.canvas.bind("<B1-Motion>",     self._mouse_drag)

        self._flbl = tk.Label(self, text="", bg="#010108", fg="#004466",
                              font=("Courier New", 9))
        self._flbl.pack(pady=(0,2))

        bot = tk.Frame(self, bg="#04040E", height=32)
        bot.pack(fill="x"); bot.pack_propagate(False)
        tk.Label(bot, text="drag to rotate  ·  JARVIS render",
                 bg="#04040E", fg="#002233",
                 font=("Courier New", 7)).pack(side="left", padx=12)
        tk.Button(bot, text="CLOSE  ✕", bg="#04040E", fg="#003344",
                  activebackground="#080010", activeforeground="#00CFFF",
                  font=("Courier New", 8, "bold"), bd=0, relief="flat",
                  command=self._close).pack(side="right", padx=12)

    # ── Shape ─────────────────────────────────────────────────────────────────────

    def set_shape(self, key):
        entry = _SHAPES.get(key)
        if not entry:
            return
        fn, label, formula = entry[0], entry[1], entry[2]
        result = fn()
        if len(result) == 4:
            self._verts, self._edges, self._faces, self._scale = result
        else:
            self._verts, self._edges, self._faces = result[:3]
        self._label_str   = label
        self._formula_str = formula
        self._ry          = 0.0
        self._lbl.configure(text=label.upper())
        self._flbl.configure(text=formula)

    # ── Input ─────────────────────────────────────────────────────────────────────

    def _mouse_press(self, e):
        self._drag_start = (e.x, e.y, self._ry, self._rx)

    def _mouse_drag(self, e):
        if self._drag_start:
            dx = e.x - self._drag_start[0]; dy = e.y - self._drag_start[1]
            self._ry = self._drag_start[2] + dx*0.010
            self._rx = self._drag_start[3] - dy*0.010

    def _win_press(self, e):
        self._win_drag = (e.x_root, e.y_root)

    def _win_move(self, e):
        if self._win_drag:
            dx = e.x_root - self._win_drag[0]; dy = e.y_root - self._win_drag[1]
            self.geometry(f"+{self.winfo_x()+dx}+{self.winfo_y()+dy}")
            self._win_drag = (e.x_root, e.y_root)

    def _close(self):
        self._alive = False
        try: self.destroy()
        except Exception: pass

    # ── Animate ───────────────────────────────────────────────────────────────────

    def _animate(self):
        if not self._alive:
            return
        self._ry += 0.010   # slightly slower for elegance
        t = time.time() - self._t0

        # scanning beam sweeps top→bottom and bounces
        self._scan_y += self._scan_dir * 0.004
        if self._scan_y >= 0.88:
            self._scan_dir = -1
        elif self._scan_y <= 0.12:
            self._scan_dir = 1

        try:
            img = _draw_frame(
                self._verts, self._edges, self._faces,
                self._scale, self._ry, self._rx, t, self._scan_y
            )
            self._photo = ImageTk.PhotoImage(img)
            self.canvas.delete("all")
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)
        except Exception as exc:
            print(f"[G.I.L. 3D] Render error: {exc}")
        self.after(16, self._animate)   # ≈60fps

    def position_beside(self, parent):
        self.update_idletasks()
        x = parent.winfo_x() + parent.winfo_width() + 10
        y = parent.winfo_y()
        if x + PANEL_W + 24 > self.winfo_screenwidth():
            x = parent.winfo_x() - PANEL_W - 34
        self.geometry(f"+{max(0,x)}+{max(0,y)}")
