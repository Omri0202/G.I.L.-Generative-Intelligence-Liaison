"""
dev_docker.py — G.I.L. developer tools
Docker container and compose operations.
"""

import subprocess
from logger import get as _get_log

log = _get_log("dev.docker")


def _run(*args: str) -> tuple[str, str, int]:
    try:
        r = subprocess.run(["docker"] + list(args), capture_output=True,
                           text=True, timeout=30, encoding="utf-8", errors="replace")
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except FileNotFoundError:
        return "", "Docker is not installed or not running.", 1
    except Exception as exc:
        return "", str(exc), 1


def containers(all_containers: bool = False) -> str:
    args = ["ps", "--format", "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"]
    if all_containers:
        args.insert(1, "-a")
    out, err, code = _run(*args)
    if code != 0:
        return f"Docker error: {err}"
    lines = out.splitlines()
    if len(lines) <= 1:
        return "No containers running." if not all_containers else "No containers found."
    return f"{len(lines)-1} container(s):\n" + "\n".join(lines[1:])


def start(name: str) -> str:
    # Try compose first, then direct container start
    out, err, code = _run("compose", "up", "-d", name)
    if code != 0:
        out, err, code = _run("start", name)
    if code != 0:
        return f"Could not start '{name}': {err}"
    return f"Started '{name}'."


def stop(name: str) -> str:
    out, err, code = _run("compose", "stop", name)
    if code != 0:
        out, err, code = _run("stop", name)
    if code != 0:
        return f"Could not stop '{name}': {err}"
    return f"Stopped '{name}'."


def logs(name: str, tail: int = 30) -> str:
    out, err, code = _run("logs", "--tail", str(tail), "--timestamps", name)
    if code != 0:
        return f"Could not get logs for '{name}': {err}"
    content = (out or err)
    if not content:
        return f"No logs for '{name}'."
    return content[-2000:]


def compose_up() -> str:
    out, err, code = _run("compose", "up", "-d")
    if code != 0:
        return f"Compose up failed: {err[:300]}"
    return "All services started."


def compose_down() -> str:
    out, err, code = _run("compose", "down")
    if code != 0:
        return f"Compose down failed: {err[:300]}"
    return "All services stopped."


def images() -> str:
    out, err, code = _run("images", "--format", "{{.Repository}}:{{.Tag}}\t{{.Size}}")
    if code != 0:
        return f"Docker error: {err}"
    lines = out.splitlines()[:10]
    if not lines:
        return "No Docker images found."
    return f"{len(lines)} image(s): " + " | ".join(lines)
