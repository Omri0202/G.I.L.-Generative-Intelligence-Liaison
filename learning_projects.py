"""
learning_projects.py — Project G.I.L.
Persistent learning project storage — saves conversations, links, and resources
per subject/project so GIL can pick up exactly where you left off.
"""

import json
import os
from datetime import datetime
from pathlib import Path

_DATA_DIR = Path(__file__).parent / "data" / "learning_projects"
_DATA_DIR.mkdir(parents=True, exist_ok=True)


def _safe_name(name: str) -> str:
    return name.lower().strip().replace(" ", "_").replace("/", "_")


def _path(name: str) -> Path:
    return _DATA_DIR / f"{_safe_name(name)}.json"


def load(name: str) -> dict:
    p = _path(name)
    if p.exists():
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "name":          name,
        "created":       _now(),
        "last_accessed": _now(),
        "sessions":      [],
        "resources":     [],
        "notes":         [],
    }


def save(data: dict) -> None:
    data["last_accessed"] = _now()
    p = _path(data["name"])
    with open(p, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def add_conversation(name: str, user_msg: str, gil_msg: str) -> None:
    data  = load(name)
    today = datetime.now().strftime("%Y-%m-%d")
    session = next((s for s in data["sessions"] if s["date"] == today), None)
    if session is None:
        session = {"date": today, "conversations": []}
        data["sessions"].append(session)
    session["conversations"].append({
        "user": user_msg[:300],
        "gil":  gil_msg[:300],
        "time": datetime.now().strftime("%H:%M"),
    })
    data["sessions"] = data["sessions"][-10:]   # keep last 10 sessions
    save(data)


def add_resource(name: str, kind: str, url: str, title: str = "") -> None:
    """kind: 'video', 'url', 'search', '3d_model'"""
    data = load(name)
    if any(r.get("url") == url for r in data["resources"]):
        return   # no duplicates
    data["resources"].insert(0, {
        "type":  kind,
        "url":   url,
        "title": title or url,
        "added": _now(),
    })
    data["resources"] = data["resources"][:50]   # cap at 50
    save(data)


def add_note(name: str, note: str) -> None:
    data = load(name)
    data["notes"].insert(0, {"text": note, "added": _now()})
    data["notes"] = data["notes"][:20]
    save(data)


def get_context_summary(name: str) -> str:
    """Returns a short summary of recent activity for injection into the LLM prompt."""
    data = load(name)
    lines = [f"PROJECT: {data['name']}"]

    if data["sessions"]:
        last = data["sessions"][-1]
        lines.append(f"Last session: {last['date']}")
        for c in last["conversations"][-3:]:
            lines.append(f"  User: {c['user'][:80]}")
            lines.append(f"  G.I.L.: {c['gil'][:80]}")

    if data["resources"]:
        lines.append("Recent resources:")
        for r in data["resources"][:5]:
            lines.append(f"  [{r['type']}] {r['title'][:60]}")

    return "\n".join(lines)


def list_all() -> list[dict]:
    projects = []
    for f in sorted(_DATA_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            with open(f, encoding="utf-8") as fp:
                d = json.load(fp)
                projects.append({
                    "name":          d["name"],
                    "last_accessed": d.get("last_accessed", ""),
                    "sessions":      len(d.get("sessions", [])),
                    "resources":     len(d.get("resources", [])),
                })
        except Exception:
            pass
    return projects


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")
