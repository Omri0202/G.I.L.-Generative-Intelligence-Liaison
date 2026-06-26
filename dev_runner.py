"""
dev_runner.py — G.I.L. developer tools
Execute terminal commands and capture output for brain analysis.
Detects project type to run the right test/start/build commands.
"""

import subprocess
import time
from pathlib import Path
from logger import get as _get_log

log = _get_log("dev.runner")


def run(command: str, cwd: str | None = None, timeout: int = 90) -> dict:
    """
    Execute a shell command.
    Returns {command, stdout, stderr, returncode, duration, success}.
    """
    start = time.time()
    try:
        r = subprocess.run(
            command, shell=True, cwd=cwd,
            capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        return {
            "command": command,
            "stdout":  r.stdout.strip(),
            "stderr":  r.stderr.strip(),
            "returncode": r.returncode,
            "duration": round(time.time() - start, 1),
            "success":  r.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"command": command, "stdout": "", "stderr": f"Timed out after {timeout}s",
                "returncode": -1, "duration": timeout, "success": False}
    except Exception as exc:
        log.error("run failed: %s", exc)
        return {"command": command, "stdout": "", "stderr": str(exc),
                "returncode": -1, "duration": 0, "success": False}


def to_speech(result: dict) -> str:
    """Convert a run result into something GIL can say."""
    ok   = result["success"]
    secs = result["duration"]
    out  = result["stdout"]
    err  = result["stderr"]

    if ok:
        if not out:
            return f"Done in {secs}s."
        tail = " — ".join(l for l in out.splitlines()[-5:] if l.strip())
        return f"Done in {secs}s. {tail[:280]}"
    else:
        text = err or out
        tail = " — ".join(l for l in text.splitlines()[-5:] if l.strip())
        return f"Failed (exit {result['returncode']}). {tail[:280]}"


def detect_commands(path: str | None = None) -> dict[str, str]:
    """
    Detect what commands to use for this project.
    Returns mapping: {test, start, build, install, lint}.
    """
    p = Path(path or Path.cwd())

    if (p / "package.json").exists():
        pkg = {}
        try:
            import json
            pkg = json.loads((p / "package.json").read_text(encoding="utf-8"))
        except Exception:
            pass
        scripts = pkg.get("scripts", {})
        return {
            "test":    scripts.get("test",    "npm test"),
            "start":   scripts.get("start",   "npm start"),
            "build":   scripts.get("build",   "npm run build"),
            "install": "npm install",
            "lint":    scripts.get("lint",    "npm run lint"),
        }

    if (p / "requirements.txt").exists() or (p / "pyproject.toml").exists():
        return {
            "test":    "pytest -v",
            "start":   "python main.py",
            "build":   "python -m build",
            "install": "pip install -r requirements.txt",
            "lint":    "flake8 .",
        }

    if (p / "Cargo.toml").exists():
        return {"test": "cargo test", "start": "cargo run",
                "build": "cargo build", "install": "", "lint": "cargo clippy"}

    if (p / "go.mod").exists():
        return {"test": "go test ./...", "start": "go run .",
                "build": "go build", "install": "go mod tidy", "lint": "golint ./..."}

    if (p / "Makefile").exists():
        return {"test": "make test", "start": "make run",
                "build": "make build", "install": "make install", "lint": "make lint"}

    return {"test": "", "start": "", "build": "", "install": "", "lint": ""}


def run_tests(path: str | None = None) -> str:
    cmds = detect_commands(path)
    cmd  = cmds.get("test", "")
    if not cmd:
        return "Could not detect a test command for this project."
    log.info("running tests: %s", cmd)
    result = run(cmd, cwd=path)
    return to_speech(result)


def run_start(path: str | None = None) -> str:
    cmds = detect_commands(path)
    cmd  = cmds.get("start", "")
    if not cmd:
        return "Could not detect a start command for this project."
    log.info("starting project: %s", cmd)
    # Start detached — don't wait for it to finish
    try:
        subprocess.Popen(cmd, shell=True, cwd=path)
        return f"Started: {cmd}"
    except Exception as exc:
        return f"Start failed: {exc}"


def check_ports() -> str:
    """List processes listening on common dev ports."""
    result = run("netstat -ano | findstr LISTENING")
    if not result["success"] or not result["stdout"]:
        return "Could not check ports."
    # Filter to common dev ports
    DEV_PORTS = {"3000", "3001", "4000", "5000", "5173", "8000", "8080",
                 "8443", "9000", "9229", "27017", "5432", "3306", "6379"}
    lines = []
    for l in result["stdout"].splitlines():
        for p in DEV_PORTS:
            if f":{p} " in l or f":{p}\t" in l:
                lines.append(l.strip())
                break
    if not lines:
        return "No processes on common dev ports."
    return f"{len(lines)} ports in use: " + " | ".join(lines[:8])
