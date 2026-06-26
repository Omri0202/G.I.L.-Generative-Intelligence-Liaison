"""
dev_deps.py — G.I.L. developer tools
Dependency management for npm, pip, cargo, go.
"""

import json
import subprocess
from pathlib import Path
from logger import get as _get_log

log = _get_log("dev.deps")


def _run(args: list[str], cwd: str | None = None) -> tuple[str, str, int]:
    try:
        r = subprocess.run(args, cwd=cwd, capture_output=True, text=True,
                           timeout=60, encoding="utf-8", errors="replace")
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except Exception as exc:
        return "", str(exc), 1


def detect_pm(path: str | None = None) -> str | None:
    p = Path(path or Path.cwd())
    if (p / "package.json").exists():   return "npm"
    if (p / "requirements.txt").exists() or (p / "pyproject.toml").exists(): return "pip"
    if (p / "Cargo.toml").exists():     return "cargo"
    if (p / "go.mod").exists():         return "go"
    return None


def check_outdated(path: str | None = None) -> str:
    pm = detect_pm(path)
    if pm == "npm":
        out, _, _ = _run(["npm", "outdated", "--json"], path)
        if not out or out == "{}":
            return "All npm packages are up to date."
        try:
            data = json.loads(out)
            pkgs = [f"{k}: {v['current']} -> {v['latest']}" for k, v in list(data.items())[:8]]
            return f"{len(data)} outdated: " + " | ".join(pkgs)
        except Exception:
            return out[:400]

    if pm == "pip":
        out, _, _ = _run(["pip", "list", "--outdated", "--format=columns"])
        lines = [l for l in out.splitlines() if l and not l.startswith(("Package", "---"))][:8]
        if not lines:
            return "All Python packages are up to date."
        return f"{len(lines)} outdated: " + " | ".join(l.split()[0] for l in lines)

    if pm == "cargo":
        out, _, _ = _run(["cargo", "outdated"], path)
        return out[:500] if out else "Cargo: run 'cargo outdated' to check."

    return "Could not detect package manager."


def install(package: str, path: str | None = None) -> str:
    pm = detect_pm(path)
    if pm == "npm":
        out, err, code = _run(["npm", "install", package], path)
    elif pm == "pip":
        out, err, code = _run(["pip", "install", package])
    elif pm == "cargo":
        out, err, code = _run(["cargo", "add", package], path)
    else:
        return f"Could not detect package manager to install '{package}'."
    if code != 0:
        return f"Installation failed: {err[:300]}"
    return f"Installed '{package}'."


def list_installed(path: str | None = None) -> str:
    pm = detect_pm(path)
    if pm == "npm":
        out, _, _ = _run(["npm", "list", "--depth=0"], path)
        pkgs = [l.split()[-1].split("@")[0] for l in out.splitlines()
                if ("+--" in l or "`--" in l)][:15]
        return f"{len(pkgs)} npm packages: " + ", ".join(pkgs)
    if pm == "pip":
        out, _, _ = _run(["pip", "list", "--format=columns"])
        pkgs = [l.split()[0] for l in out.splitlines()
                if l and not l.startswith(("Package", "---"))][:15]
        return f"{len(pkgs)} pip packages: " + ", ".join(pkgs)
    return "Could not list packages."
