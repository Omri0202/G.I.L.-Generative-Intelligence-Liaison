"""
dev_search.py — G.I.L. developer tools
Code search within a project. Uses ripgrep if installed, Python fallback otherwise.
"""

import os
import re
import subprocess
from pathlib import Path
from logger import get as _get_log

log = _get_log("dev.search")

_CODE_EXT = {".py",".js",".ts",".tsx",".jsx",".go",".rs",".java",
             ".c",".cpp",".h",".cs",".rb",".php",".swift",".kt",".vue"}
_SKIP_DIRS = {".git","node_modules","__pycache__",".venv","venv",
              "dist","build",".idea",".next","coverage"}


def _has_rg() -> bool:
    try:
        subprocess.run(["rg","--version"], capture_output=True, timeout=3)
        return True
    except Exception:
        return False


def _project_root() -> str:
    try:
        r = subprocess.run(["git","rev-parse","--show-toplevel"],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return str(Path.cwd())


# ── Search ────────────────────────────────────────────────────────────────────

def search(query: str, path: str | None = None, glob: str | None = None,
           max_results: int = 20) -> str:
    """Search for a string/regex in code files."""
    root = path or _project_root()

    if _has_rg():
        args = ["rg", "--line-number", "--max-count=3",
                "--max-filesize=500K", "--color=never"]
        if glob:
            args += ["-g", glob]
        args += [query, root]
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=15,
                               encoding="utf-8", errors="replace")
            out = r.stdout.strip()
        except Exception:
            out = _py_grep(query, root, max_results)
    else:
        out = _py_grep(query, root, max_results)

    if not out:
        return f"No matches for '{query}'."
    lines = out.splitlines()[:max_results]
    return f"{len(lines)} match(es):\n" + "\n".join(lines)


def find_definition(symbol: str, path: str | None = None) -> str:
    """Find where a function/class/variable is defined."""
    patterns = [
        rf"def {re.escape(symbol)}\s*[\(:]",       # Python
        rf"class {re.escape(symbol)}\b",            # Python/JS/TS/Java
        rf"function {re.escape(symbol)}\s*\(",      # JS
        rf"const {re.escape(symbol)}\s*=",          # JS/TS
        rf"type {re.escape(symbol)}\s",             # TS
        rf"interface {re.escape(symbol)}\b",        # TS
        rf"fn {re.escape(symbol)}\s*\(",            # Rust
        rf"func {re.escape(symbol)}\s*\(",          # Go
        rf"def {re.escape(symbol)}\b",              # Ruby
    ]
    root = path or _project_root()
    for pat in patterns:
        result = search(pat, root)
        if "No matches" not in result:
            return result
    return f"Could not find definition of '{symbol}'."


def find_todos(path: str | None = None) -> str:
    """Find all TODO, FIXME, HACK, BUG comments."""
    result = search(r"TODO|FIXME|HACK|BUG|XXX", path)
    return result


def project_structure(path: str | None = None, max_depth: int = 3) -> str:
    """Show the project directory tree."""
    p = Path(path or _project_root())
    lines = [str(p.name) + "/"]
    _build_tree(p, lines, "", 0, max_depth)
    return "\n".join(lines[:60])


def _build_tree(path: Path, lines: list, prefix: str, depth: int, max_depth: int) -> None:
    if depth >= max_depth:
        return
    try:
        entries = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        entries = [e for e in entries if e.name not in _SKIP_DIRS][:30]
        for i, entry in enumerate(entries):
            is_last   = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            lines.append(prefix + connector + entry.name + ("/" if entry.is_dir() else ""))
            if entry.is_dir():
                extension = "    " if is_last else "│   "
                _build_tree(entry, lines, prefix + extension, depth + 1, max_depth)
    except PermissionError:
        pass


def _py_grep(pattern: str, root: str, max_results: int = 20) -> str:
    results = []
    try:
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for fname in files:
                if Path(fname).suffix not in _CODE_EXT:
                    continue
                fpath = os.path.join(dirpath, fname)
                try:
                    with open(fpath, encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            if re.search(pattern, line, re.IGNORECASE):
                                rel = os.path.relpath(fpath, root)
                                results.append(f"{rel}:{lineno}: {line.rstrip()[:120]}")
                                if len(results) >= max_results:
                                    return "\n".join(results)
                except Exception:
                    pass
    except Exception:
        pass
    return "\n".join(results)
