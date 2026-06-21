"""
release.py — G.I.L. release tool
Bumps the version, builds the exe, creates a GitHub release, uploads GIL.zip.

Usage:
    python release.py 1.1.0              # full release
    python release.py 1.1.0 --notes "Fixed Hebrew TTS, improved chat UI"
    python release.py --check            # just show current version

Requirements:
    pip install requests
    gh CLI installed and authenticated  (https://cli.github.com)
"""

import sys
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent


# ── Helpers ───────────────────────────────────────────────────────────────────

def _read_version() -> str:
    from version import VERSION
    return VERSION


def _write_version(new_ver: str) -> None:
    vfile = ROOT / "version.py"
    text  = vfile.read_text(encoding="utf-8")
    lines = []
    for line in text.splitlines():
        if line.startswith("VERSION"):
            lines.append(f'VERSION     = "{new_ver}"')
        else:
            lines.append(line)
    vfile.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[RELEASE] version.py updated → {new_ver}")


def _build() -> Path:
    print("[RELEASE] Building GIL.exe …")
    result = subprocess.run([sys.executable, str(ROOT / "build.py")], cwd=str(ROOT))
    if result.returncode != 0:
        sys.exit("[RELEASE] Build failed — fix errors above and try again.")
    zip_path = ROOT / "dist" / "GIL.zip"
    if not zip_path.exists():
        print("[RELEASE] Zipping dist/GIL …")
        shutil.make_archive(
            str(ROOT / "dist" / "GIL"),
            "zip",
            str(ROOT / "dist"),
            "GIL",
        )
    print(f"[RELEASE] Package ready: {zip_path}  ({zip_path.stat().st_size // 1_048_576} MB)")
    return zip_path


def _git_tag_and_push(version: str) -> None:
    tag = f"v{version}"
    subprocess.run(["git", "add", "version.py"], cwd=str(ROOT), check=True)
    subprocess.run(
        ["git", "commit", "-m", f"chore: bump version to {version}"],
        cwd=str(ROOT), check=True,
    )
    subprocess.run(["git", "tag", tag], cwd=str(ROOT), check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=str(ROOT), check=True)
    subprocess.run(["git", "push", "origin", tag], cwd=str(ROOT), check=True)
    print(f"[RELEASE] Tagged and pushed {tag}")


def _gh_release(version: str, zip_path: Path, notes: str) -> None:
    tag = f"v{version}"
    print(f"[RELEASE] Creating GitHub release {tag} …")

    from version import GITHUB_REPO
    repo = GITHUB_REPO

    # Create release
    result = subprocess.run(
        [
            "gh", "release", "create", tag,
            str(zip_path),
            "--repo", repo,
            "--title", f"G.I.L. {tag}",
            "--notes", notes or f"G.I.L. version {version}",
        ],
        cwd=str(ROOT),
    )
    if result.returncode != 0:
        print("[RELEASE] gh release failed.")
        print("  Make sure 'gh' CLI is installed and you're logged in:")
        print("  https://cli.github.com")
        print(f"  Then manually upload {zip_path} to a release tagged {tag}")
    else:
        print(f"[RELEASE] Published! https://github.com/{repo}/releases/tag/{tag}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    args  = sys.argv[1:]

    if not args or "--check" in args:
        print(f"Current version: {_read_version()}")
        return

    new_version = args[0]
    notes       = ""
    if "--notes" in args:
        idx   = args.index("--notes")
        notes = args[idx + 1] if idx + 1 < len(args) else ""

    # Validate semver-ish
    parts = new_version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        sys.exit(f"[RELEASE] Version must be X.Y.Z — got: {new_version!r}")

    print("=" * 52)
    print(f"  Releasing G.I.L. v{new_version}")
    print("=" * 52)

    _write_version(new_version)
    zip_path = _build()
    _git_tag_and_push(new_version)
    _gh_release(new_version, zip_path, notes)

    print("\n[RELEASE] Done.")
    print(f"  All existing users will see the update notification")
    print(f"  the next time they launch GIL.")


if __name__ == "__main__":
    main()
