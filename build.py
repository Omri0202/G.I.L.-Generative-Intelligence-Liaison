"""
build.py — G.I.L. packager
Run this once to create a distributable Windows app.

Usage:
    python build.py

Output:
    dist/GIL/          ← the distributable folder
    dist/GIL/GIL.exe   ← the launcher

Send the whole dist/GIL/ folder (or zip it) to anyone.
They double-click GIL.exe, complete the setup wizard, and they're running.
"""

import os
import sys
import shutil
import subprocess
from pathlib import Path

ROOT    = Path(__file__).parent
DIST    = ROOT / "dist" / "GIL"
DATA    = ROOT / "data"

# ── Pre-flight checks ─────────────────────────────────────────────────────────

def check_prereqs() -> None:
    print("\n[BUILD] Checking prerequisites...")

    # Python version
    if sys.version_info < (3, 10):
        sys.exit("[BUILD] Python 3.10+ required.")
    print(f"  Python {sys.version.split()[0]}  OK")

    # PyInstaller
    try:
        import PyInstaller
        print(f"  PyInstaller {PyInstaller.__version__}  OK")
    except ImportError:
        print("  PyInstaller not found — installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("  PyInstaller installed  OK")

    # Core packages
    required = [
        "customtkinter", "PIL", "pystray", "requests",
        "dotenv", "edge_tts", "speech_recognition",
    ]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        sys.exit(f"[BUILD] Missing packages: {', '.join(missing)}\n"
                 f"Run: pip install {' '.join(missing)}")
    print(f"  Core packages  OK")

    # Icon
    if not (DATA / "gil.ico").exists():
        sys.exit("[BUILD] data/gil.ico not found. Run python _make_icon.py first.")
    print("  gil.ico  OK\n")


# ── Collect data files ────────────────────────────────────────────────────────

def collect_datas() -> list[tuple[str, str]]:
    """Return list of (source, dest_dir) tuples for PyInstaller."""
    datas = []

    # GIL assets
    for filename in ["gil.ico", "gil_logo.png", "gil_icon_hq.png"]:
        src = DATA / filename
        if src.exists():
            datas.append((str(src), "data"))

    # Google credentials (bundled app credentials — NOT the user's token)
    gcreds = DATA / "gmail_credentials.json"
    if gcreds.exists():
        datas.append((str(gcreds), "data"))

    # Default config skeleton (created if missing at runtime)
    config = DATA / "gil_config.json"
    if config.exists():
        datas.append((str(config), "data"))

    # Default user profile template
    profile = DATA / "user_profile.json"
    if profile.exists():
        datas.append((str(profile), "data"))

    # CustomTkinter — needs its theme/image files
    try:
        import customtkinter
        ctk_dir = Path(customtkinter.__file__).parent
        datas.append((str(ctk_dir), "customtkinter"))
        print(f"[BUILD] CustomTkinter assets: {ctk_dir}")
    except Exception as exc:
        print(f"[BUILD] Warning: could not locate customtkinter: {exc}")

    # edge_tts has no bundled data, but let's grab any package data
    try:
        import edge_tts
        etss_dir = Path(edge_tts.__file__).parent
        if (etss_dir / "voices.json").exists():
            datas.append((str(etss_dir / "voices.json"), "edge_tts"))
    except Exception:
        pass

    return datas


# ── Hidden imports ────────────────────────────────────────────────────────────

HIDDEN = [
    # Windows / tray
    "pystray._win32",
    "win32gui", "win32con", "win32api", "win32com",
    # Audio
    "pyaudio", "speech_recognition",
    "speech_recognition.audio",
    # Network
    "requests", "urllib3", "certifi", "charset_normalizer", "idna",
    # TTS
    "edge_tts", "edge_tts.communicate",
    "aiohttp", "aiohttp.cookiejar", "aiohttp.connector",
    "aiohttp.client_exceptions",
    # Image
    "PIL._tkinter_finder",
    "PIL.Image", "PIL.ImageTk", "PIL.ImageDraw",
    "PIL.ImageFilter", "PIL.ImageEnhance",
    # Async
    "asyncio", "asyncio.windows_events", "asyncio.windows_utils",
    # Tkinter / CTk
    "customtkinter", "tkinter", "tkinter.ttk",
    # DB
    "sqlite3",
    # Env
    "dotenv",
    # Encoding
    "encodings.utf_8", "encodings.latin_1", "encodings.cp1252",
    "encodings.utf_16",
    # keyboard
    "keyboard",
    # GIL application modules (resolved via dynamic imports)
    "fast_paths", "wake_phrase", "action_handlers", "tray_manager",
    "conversation_engine", "setup_wizard", "chat_history",
    "gil_brain", "gui", "voice", "ears", "actions", "auth",
    "credentials", "memory", "preferences", "session_manager",
    "context_engine", "goal_tracker", "proactive", "modes",
    "reminders", "triggers", "tasks", "learning_projects",
    "viewer3d", "studio3d", "screen", "location", "weather",
    "news", "gcalendar", "gmail_recap", "whatsapp_recap",
    "briefing", "spotify_control", "pc_control", "notes",
    "webgen", "eyes", "face_id", "gestures", "visualizer",
    "meeting_detector", "watcher",
    # Optional heavy packages (gracefully absent at runtime)
    "cv2", "numpy",
]


# ── Spec generation ───────────────────────────────────────────────────────────

def write_spec(datas: list[tuple[str, str]]) -> Path:
    spec_path = ROOT / "gil.spec"

    datas_repr = ",\n        ".join(
        f"({src!r}, {dst!r})" for src, dst in datas
    )
    hidden_repr = ",\n    ".join(f"{h!r}" for h in HIDDEN)

    spec_content = f"""\
# -*- mode: python ; coding: utf-8 -*-
# Auto-generated by build.py — do not edit by hand.

block_cipher = None

a = Analysis(
    [{str(ROOT / 'main.py')!r}],
    pathex=[{str(ROOT)!r}],
    binaries=[],
    datas=[
        {datas_repr}
    ],
    hiddenimports=[
    {hidden_repr}
    ],
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[
        # ── PyTorch / ML stack (not used by GIL) ──────────────────────────
        'torch', 'torchvision', 'torchaudio', 'torchtext',
        'transformers', 'diffusers', 'accelerate', 'peft',
        'tokenizers', 'huggingface_hub', 'hf_xet', 'timm',
        'xformers', 'triton', 'bitsandbytes',
        # ── ONNX / inference runtimes ──────────────────────────────────────
        'onnxruntime', 'onnx', 'onnxruntime_extensions',
        # ── Browser automation ─────────────────────────────────────────────
        'playwright', 'greenlet', 'gevent',
        # ── Scientific / image processing ──────────────────────────────────
        'scipy', 'skimage', 'scikit_image', 'scikit_learn', 'sklearn',
        'matplotlib', 'pandas', 'seaborn', 'plotly', 'bokeh',
        # ── Testing / dev tools ────────────────────────────────────────────
        'pytest', 'IPython', 'jupyter', 'notebook', 'ipykernel',
        'setuptools', 'pkg_resources', 'pip',
        # ── Qt / wx (we use tkinter/CTk) ───────────────────────────────────
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
        # ── Other unused ───────────────────────────────────────────────────
        'docutils', 'sphinx', 'Crypto', 'nacl',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='GIL',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon={str(DATA / 'gil.ico')!r},
    version_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='GIL',
)
"""
    spec_path.write_text(spec_content, encoding="utf-8")
    print(f"[BUILD] Spec written: {spec_path}")
    return spec_path


# ── Run PyInstaller ───────────────────────────────────────────────────────────

def run_pyinstaller(spec: Path) -> None:
    print("\n[BUILD] Running PyInstaller (this takes 1-3 minutes)...\n")
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", str(spec), "--clean", "--noconfirm"],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        sys.exit("[BUILD] PyInstaller failed — see errors above.")
    print("\n[BUILD] PyInstaller finished.")


# ── Post-process ──────────────────────────────────────────────────────────────

def post_process() -> None:
    """Copy runtime-created files and create launcher scripts."""
    print("[BUILD] Post-processing...")

    # Ensure dist/GIL/data/ exists
    (DIST / "data").mkdir(parents=True, exist_ok=True)

    # Write a first-run README inside the dist folder
    readme = DIST / "README.txt"
    readme.write_text(
        "G.I.L. — Generative Intelligence Liaison\n"
        "=========================================\n\n"
        "Double-click GIL.exe to launch.\n\n"
        "First run: the setup wizard will guide you through:\n"
        "  1. Your name\n"
        "  2. Free Groq AI key (brain) — sign up at console.groq.com\n"
        "  3. Google account (optional, for email + calendar)\n\n"
        "Once set up, GIL starts with Windows automatically.\n"
        "Right-click the tray icon (bottom-right) to access settings.\n\n"
        "Hotkeys:\n"
        "  Ctrl+Shift+G  — activate voice\n"
        "  Ctrl+Shift+C  — open chat\n",
        encoding="utf-8",
    )

    # Write a simple batch launcher (alternative to direct .exe)
    launcher = DIST / "Launch GIL.bat"
    launcher.write_text(
        '@echo off\n'
        'start "" "%~dp0GIL.exe"\n',
        encoding="utf-8",
    )

    print(f"[BUILD] Output folder: {DIST}\n")


# ── Inno Setup script (optional professional installer) ───────────────────────

def write_inno_script() -> None:
    iss = ROOT / "gil_installer.iss"
    iss.write_text(f"""\
; Inno Setup script for G.I.L.
; Compile with Inno Setup 6 (https://jrsoftware.org/isinfo.php)
; Produces a single GIL_Setup.exe installer.

[Setup]
AppName=G.I.L.
AppVersion=1.0
AppPublisher=Omri
DefaultDirName={{autopf}}\\GIL
DefaultGroupName=G.I.L.
UninstallDisplayIcon={{app}}\\GIL.exe
OutputBaseFilename=GIL_Setup
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupIconFile={str(DATA / 'gil.ico')}
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"
Name: "startup";     Description: "Start G.I.L. with Windows"; GroupDescription: "Startup:"

[Files]
Source: "{str(DIST)}\\*"; DestDir: "{{app}}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\G.I.L.";        Filename: "{{app}}\\GIL.exe"
Name: "{{group}}\\Uninstall GIL";  Filename: "{{uninstallexe}}"
Name: "{{commondesktop}}\\G.I.L."; Filename: "{{app}}\\GIL.exe"; Tasks: desktopicon
Name: "{{userstartup}}\\G.I.L.";   Filename: "{{app}}\\GIL.exe"; Tasks: startup

[Run]
Filename: "{{app}}\\GIL.exe"; Description: "Launch G.I.L. now"; Flags: postinstall nowait skipifsilent
""", encoding="utf-8")
    print(f"[BUILD] Inno Setup script: {iss}")
    print("        Compile it with Inno Setup 6 to create GIL_Setup.exe\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * 52)
    print("  G.I.L. — Build System")
    print("=" * 52)

    check_prereqs()
    datas   = collect_datas()
    spec    = write_spec(datas)
    run_pyinstaller(spec)
    post_process()
    write_inno_script()

    print("=" * 52)
    print("  BUILD COMPLETE")
    print("=" * 52)
    print(f"\n  Distributable folder: {DIST}")
    print("  Share the whole 'GIL' folder — anyone can run GIL.exe.\n")
    print("  For a single-file installer:")
    print("  1. Install Inno Setup 6  (https://jrsoftware.org)")
    print("  2. Open gil_installer.iss - Compile")
    print("  3. Share GIL_Setup.exe\n")


if __name__ == "__main__":
    main()
