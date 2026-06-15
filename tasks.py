"""
tasks.py — Project G.I.L.
Project and task management. Persistent JSON storage.
"""

import json
import os
import uuid
from datetime import datetime

_PATH = os.path.join(os.path.dirname(__file__), "data", "tasks.json")


def _load() -> dict:
    try:
        os.makedirs(os.path.dirname(_PATH), exist_ok=True)
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"projects": {}, "tasks": []}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    with open(_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def create_project(name: str) -> dict:
    data  = _load()
    key   = name.lower().strip().replace(" ", "_")
    proj  = {"key": key, "name": name.strip(), "created": datetime.now().isoformat()}
    data["projects"][key] = proj
    _save(data)
    return proj


def add_task(text: str, project_key: str = "") -> dict:
    data = _load()
    project_key = (project_key or "").lower().strip().replace(" ", "_")

    if not project_key:
        if data["projects"]:
            project_key = next(iter(data["projects"]))
        else:
            p = create_project("General")
            data = _load()
            project_key = p["key"]

    if project_key not in data["projects"]:
        create_project(project_key)
        data = _load()

    task = {
        "id":      str(uuid.uuid4())[:8],
        "text":    text.strip(),
        "project": project_key,
        "done":    False,
        "created": datetime.now().isoformat(),
    }
    data["tasks"].append(task)
    _save(data)
    return task


def complete_task(text_or_id: str) -> bool:
    data  = _load()
    lower = text_or_id.lower()
    for t in data["tasks"]:
        if t["id"] == text_or_id or lower in t["text"].lower():
            t["done"] = True
            _save(data)
            return True
    return False


def delete_task(text_or_id: str) -> bool:
    data   = _load()
    lower  = text_or_id.lower()
    before = len(data["tasks"])
    data["tasks"] = [
        t for t in data["tasks"]
        if t["id"] != text_or_id and lower not in t["text"].lower()
    ]
    if len(data["tasks"]) < before:
        _save(data)
        return True
    return False


def get_all() -> dict:
    return _load()


def get_open_tasks() -> list:
    return [t for t in _load()["tasks"] if not t["done"]]
