"""
dev_git.py — G.I.L. developer tools
Git operations via the local git CLI.
All functions return human-readable strings GIL can speak or show.
"""

import re
import subprocess
from pathlib import Path
from logger import get as _get_log

log = _get_log("dev.git")


# ── Internal helpers ──────────────────────────────────────────────────────────

def _run(args: list[str], cwd: str | None = None) -> tuple[str, str, int]:
    try:
        root = cwd or _root()
        r = subprocess.run(
            ["git"] + args,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except FileNotFoundError:
        return "", "Git is not installed.", 1
    except Exception as exc:
        log.error("git command failed: %s", exc)
        return "", str(exc), 1


def _root() -> str | None:
    """Find the git root from the current working directory or common project paths."""
    for candidate in [Path.cwd(), Path.home() / "Desktop", Path.home() / "Documents"]:
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=str(candidate), capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def status(cwd: str | None = None) -> str:
    out, err, code = _run(["status", "--short", "--branch"], cwd)
    if code != 0:
        return f"Git: {err or 'not a git repository'}"

    lines = out.splitlines()
    branch_line = next((l for l in lines if l.startswith("##")), "")
    changes     = [l for l in lines if not l.startswith("##")]

    result = []
    if branch_line:
        result.append(f"Branch: {branch_line.replace('## ', '')}")

    modified  = [l[3:] for l in changes if l.startswith((" M", "M "))]
    staged    = [l[3:] for l in changes if l.startswith(("A ", "AM"))]
    deleted   = [l[3:] for l in changes if l.startswith((" D", "D "))]
    untracked = [l[3:] for l in changes if l.startswith("??")]

    if modified:  result.append(f"Modified ({len(modified)}): {', '.join(modified[:5])}")
    if staged:    result.append(f"Staged ({len(staged)}): {', '.join(staged[:5])}")
    if deleted:   result.append(f"Deleted ({len(deleted)}): {', '.join(deleted[:3])}")
    if untracked: result.append(f"Untracked: {len(untracked)} files")
    if not changes: result.append("Clean — nothing to commit")

    return ". ".join(result)


def commit(message: str, cwd: str | None = None) -> str:
    _run(["add", "-u"], cwd)          # stage tracked changes
    out, err, code = _run(["commit", "-m", message], cwd)
    if code != 0:
        return f"Commit failed: {err}"
    first = out.splitlines()[0] if out else "Committed."
    return first


def push(target: str = "", cwd: str | None = None) -> str:
    args = ["push"]
    if target:
        parts = target.split()
        args += parts[:2]
    out, err, code = _run(args, cwd)
    if code != 0:
        return f"Push failed: {err}"
    return out or "Pushed successfully."


def pull(cwd: str | None = None) -> str:
    out, err, code = _run(["pull"], cwd)
    if code != 0:
        return f"Pull failed: {err}"
    return out or "Already up to date."


def log_recent(n: int = 8, cwd: str | None = None) -> str:
    fmt = "%h  %s  (%cr, %an)"
    out, err, code = _run(["log", f"-{n}", f"--pretty=format:{fmt}"], cwd)
    if code != 0:
        return f"Log failed: {err}"
    return out or "No commits yet."


def diff_stat(path: str = "", cwd: str | None = None) -> str:
    args = ["diff", "--stat"]
    if path:
        args.append(path)
    out, err, code = _run(args, cwd)
    if code != 0:
        return f"Diff failed: {err}"
    return out or "No changes."


def diff_content(path: str = "", cwd: str | None = None) -> str:
    """Full diff content — for passing to the brain."""
    args = ["diff"]
    if path:
        args.append(path)
    out, _, _ = _run(args, cwd)
    return out[:4000] if out else "No changes."


def branch_list(cwd: str | None = None) -> str:
    out, err, code = _run(["branch", "-a"], cwd)
    if code != 0:
        return f"Branch list failed: {err}"
    return out or "No branches."


def branch_create(name: str, cwd: str | None = None) -> str:
    safe = re.sub(r"[^\w\-]", "-", name.lower().replace(" ", "-"))
    _, err, code = _run(["checkout", "-b", safe], cwd)
    if code != 0:
        return f"Branch creation failed: {err}"
    return f"Switched to new branch '{safe}'."


def branch_switch(name: str, cwd: str | None = None) -> str:
    _, err, code = _run(["checkout", name], cwd)
    if code != 0:
        return f"Switch failed: {err}"
    return f"Switched to '{name}'."


def stash_save(cwd: str | None = None) -> str:
    out, err, code = _run(["stash"], cwd)
    if code != 0:
        return f"Stash failed: {err}"
    return out or "Changes stashed."


def stash_pop(cwd: str | None = None) -> str:
    out, err, code = _run(["stash", "pop"], cwd)
    if code != 0:
        return f"Stash pop failed: {err}"
    return out or "Stash applied."
