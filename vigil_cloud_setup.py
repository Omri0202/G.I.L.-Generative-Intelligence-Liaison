"""
vigil_cloud_setup.py — move Vigil to a free server of its own, so it keeps
watch and answers with the PC off.

    python vigil_cloud_setup.py            set it up (or bring it up to date)
    python vigil_cloud_setup.py deploy     run the latest code there
    python vigil_cloud_setup.py status     is it up, and what's it running
    python vigil_cloud_setup.py logs       its recent log
    python vigil_cloud_setup.py pull       bring Vigil's memory back to this PC
    python vigil_cloud_setup.py home       ...and have this PC host Vigil again

What it sets up, all free and without a card:
  - a private GitHub repository for Vigil's memory (encrypted — GitHub only
    sees ciphertext), written with a deploy key that opens that one repository,
  - a Render web service on the free plan, from G.I.L.'s public code, in a
    workspace of the user's choosing (a workspace of its own keeps Vigil's
    750 free hours a month from being shared),
  - data/vigil_cloud.json on this PC: where the server is and the PC's key.
    Never committed.

Needs RENDER_API_KEY in .env, and the GitHub CLI signed in.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import requests

ROOT = Path(__file__).parent
DATA = ROOT / "data"
CONFIG = DATA / "vigil_cloud.json"
API = "https://api.render.com/v1"
SERVICE = "gil-vigil"
DATA_REPO = "gil-vigil-data"
REGION = "frankfurt"                 # the nearest to Israel
PYTHON = "3.14.3"
#: Israel's clock, as a rule rather than a zone name: needs no zone files on the host.
TZ = "IST-2IDT,M3.4.4/26,M10.5.0"
KEYS_FROM_ENV = ("GROQ_API_KEY", "GROQ_API_KEY_2", "TAVILY_API_KEY")


def say(text: str) -> None:
    print(text, flush=True)


def fail(text: str) -> None:
    sys.exit(f"\n✗ {text}")


# ── local config ──────────────────────────────────────────────────────────────

def load() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    tmp.replace(CONFIG)


# ── Render ────────────────────────────────────────────────────────────────────

def _render_key() -> str:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        fail("RENDER_API_KEY isn't in .env — make one in Render (Account Settings → API Keys) "
             "and add it as RENDER_API_KEY=...")
    return key


def render(method: str, path: str, **kw):
    r = requests.request(method, API + path, timeout=60, **kw,
                         headers={"Authorization": f"Bearer {_render_key()}",
                                  "Accept": "application/json"})
    if not r.ok:
        fail(f"Render said {r.status_code} to {method} {path}: {r.text[:400]}")
    return r.json() if r.text else None


def pick_workspace(cfg: dict) -> str:
    if cfg.get("owner_id"):
        return cfg["owner_id"]
    owners = [o["owner"] for o in render("GET", "/owners?limit=50")]
    want = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--workspace=")), "")
    chosen = ([o for o in owners if o["id"] == want] if want else
              [o for o in owners if "vigil" in (o.get("name") or "").lower()] or
              (owners if len(owners) == 1 else []))
    if not chosen:
        listing = "\n".join(f"  {o['id']}  {o.get('name')}" for o in owners)
        fail(f"Which Render workspace? Run again with --workspace=<id>:\n{listing}")
    owner = chosen[0]
    others = [s["service"] for s in render("GET", f"/services?ownerId={owner['id']}&limit=50")
              if s["service"]["name"] != SERVICE]
    free = [s["name"] for s in others if (s.get("serviceDetails") or {}).get("plan") == "free"]
    if free and "--shared" not in sys.argv:
        fail(f"The workspace “{owner.get('name')}” already runs {', '.join(free)} on the free "
             "plan. Its 750 free hours a month would be shared — Vigil alone uses ~744 in a long "
             "month, and when they run out Render pauses every free service there until the "
             "month ends. Make a workspace for Vigil in Render (top-left workspace menu → New "
             "workspace) and run this again, or add --shared to go ahead anyway.")
    say(f"• Render workspace: {owner.get('name')} ({owner['id']})")
    return owner["id"]


def find_service(owner_id: str) -> dict | None:
    for s in render("GET", f"/services?ownerId={owner_id}&name={SERVICE}&limit=20"):
        if s["service"]["name"] == SERVICE:
            return s["service"]
    return None


def env_vars(cfg: dict) -> list[dict]:
    import vigil
    voice = ""
    try:
        voice = json.loads((DATA / "gil_config.json").read_text(encoding="utf-8")).get("tts_voice", "")
    except (OSError, ValueError):
        pass
    env = {"GIL_CLOUD": "1", "GIL_VIGIL_KEY": cfg["key"],
           "GIL_DATA_REPO": cfg["data_repo"], "GIL_DATA_SECRET": cfg["data_secret"],
           "GIL_DATA_DEPLOY": cfg["deploy_key"], "GIL_USER": vigil.get_user() or "",
           "GIL_VOICE": voice, "TZ": TZ, "PYTHON_VERSION": PYTHON, "PYTHONUNBUFFERED": "1"}
    for name in KEYS_FROM_ENV:
        if os.environ.get(name):
            env[name] = os.environ[name]
    return [{"key": k, "value": v} for k, v in env.items() if v]


def wait_live(service_id: str, minutes: int = 20) -> bool:
    last = ""
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        deploys = render("GET", f"/services/{service_id}/deploys?limit=1")
        status = deploys[0]["deploy"]["status"] if deploys else "waiting"
        if status != last:
            say(f"  … {status.replace('_', ' ')}")
            last = status
        if status == "live":
            return True
        if status in ("build_failed", "update_failed", "canceled", "deactivated",
                      "pre_deploy_failed"):
            return False
        time.sleep(10)
    return False


def gil_repo() -> str:
    url = subprocess.run(["git", "remote", "get-url", "origin"], cwd=ROOT, capture_output=True,
                         text=True, check=True).stdout.strip()
    return url[:-4] if url.endswith(".git") else url


# ── GitHub ────────────────────────────────────────────────────────────────────

def gh(*args: str, check: bool = True) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True)
    if check and r.returncode:
        fail(f"gh {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout.strip()


def data_repo(cfg: dict) -> None:
    """The private repository Vigil's memory is kept in, and the key that
    writes to it and to nothing else."""
    if cfg.get("data_repo") and cfg.get("deploy_key"):
        return
    login = gh("api", "user", "--jq", ".login")
    full = f"{login}/{DATA_REPO}"
    exists = subprocess.run(["gh", "repo", "view", full], capture_output=True).returncode == 0
    if not exists:
        gh("repo", "create", full, "--private",
           "--description", "G.I.L. Vigil's memory — encrypted, written by the Vigil server")
        say(f"• made the private repository {full}")
    if gh("repo", "view", full, "--json", "isPrivate", "--jq", ".isPrivate") != "true":
        fail(f"{full} isn't private — make it private (or delete it) and run this again")
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.OpenSSH,
                                serialization.NoEncryption()).decode()
    public = key.public_key().public_bytes(serialization.Encoding.OpenSSH,
                                           serialization.PublicFormat.OpenSSH).decode()
    gh("api", f"repos/{full}/keys", "-f", "title=G.I.L. Vigil server",
       "-f", f"key={public}", "-F", "read_only=false")
    say("• gave the server a key to that repository only")
    cfg.update(data_repo=f"git@github.com:{full}.git",
               deploy_key=base64.b64encode(private.encode()).decode())
    save_config(cfg)


def store(cfg: dict):
    import cloud_store
    return cloud_store.Store(DATA, cfg["data_repo"], cfg["data_secret"],
                             cloud_store._key_text(cfg["deploy_key"]))


def upload(cfg: dict) -> None:
    """This PC's Vigil — watches, chat, logins — becomes the server's."""
    import cloud_store
    st = store(cfg)
    st.writable = True
    st.save(force=True)
    with tempfile.TemporaryDirectory() as tmp:          # read it back: it must open
        check = cloud_store.Store(Path(tmp), cfg["data_repo"], cfg["data_secret"], st.deploy_key)
        if check.restore() != "restored" or not (Path(tmp) / "vigil.json").exists():
            fail("the memory didn't come back the same — nothing's been switched over")
    say("• uploaded Vigil's memory (encrypted) and read it back ✓")


# ── commands ──────────────────────────────────────────────────────────────────

def setup() -> None:
    cfg = load()
    _render_key()
    cfg.setdefault("key", secrets.token_urlsafe(32))
    if not cfg.get("data_secret"):
        from cryptography.fernet import Fernet
        cfg["data_secret"] = Fernet.generate_key().decode()
    save_config({**cfg, "enabled": False})          # not linked until the server answers
    data_repo(cfg)
    cfg["owner_id"] = pick_workspace(cfg)
    upload(cfg)
    service = find_service(cfg["owner_id"])
    if service is None:
        body = {"type": "web_service", "name": SERVICE, "ownerId": cfg["owner_id"],
                "repo": gil_repo(), "branch": "main", "autoDeployTrigger": "commit",
                "buildFilter": {"paths": ["*.py", "web_chat/**", "requirements-cloud.txt"],
                                "ignoredPaths": ["tests/**"]},
                "envVars": env_vars(cfg),
                "serviceDetails": {"runtime": "python", "plan": "free", "region": REGION,
                                   "numInstances": 1, "healthCheckPath": "/health",
                                   "previews": {"generation": "off"},
                                   "envSpecificDetails": {
                                       "buildCommand": "pip install -r requirements-cloud.txt",
                                       "startCommand": "python vigil_cloud.py"}}}
        service = render("POST", "/services", json=body)["service"]
        say(f"• created the Render service {SERVICE}")
    else:
        render("PUT", f"/services/{service['id']}/env-vars", json=env_vars(cfg))
        render("POST", f"/services/{service['id']}/deploys", json={})
        say(f"• updated the Render service {SERVICE}")
    cfg.update(service_id=service["id"], url=service["serviceDetails"]["url"].rstrip("/"))
    save_config({**cfg, "enabled": False})
    say("• building and starting it (a few minutes)…")
    if not wait_live(service["id"]):
        fail("it didn't start — run `python vigil_cloud_setup.py logs` to see why")
    if not healthy(cfg):
        fail("it started but doesn't answer as Vigil — see `python vigil_cloud_setup.py logs`")
    cfg["enabled"] = True
    save_config(cfg)
    say(f"\n✓ Vigil runs at {cfg['url']} — with the PC off too.\n"
        "  Restart G.I.L. (and the watcher) so this PC links to it instead of hosting it.\n"
        "  Then open Vigil → Use on your phone, scan the new code, install the app and turn "
        "notifications on.")


def healthy(cfg: dict) -> bool:
    for _ in range(12):
        try:
            r = requests.post(cfg["url"] + "/api", timeout=30,
                              json={"method": "state", "args": []},
                              headers={"X-Vigil": "1", "X-GIL-Token": cfg["key"]})
            st = r.json().get("result") if r.ok else None
            if st and st.get("phone", {}).get("cloud"):
                say(f"• it answers ✓ ({len(st.get('messages') or [])} recent messages, "
                    f"{len(st.get('watches') or [])} watches)")
                return True
        except (requests.RequestException, ValueError):
            pass
        time.sleep(10)
    return False


def deploy() -> None:
    cfg = load()
    if not cfg.get("service_id"):
        fail("not set up yet — run `python vigil_cloud_setup.py`")
    render("POST", f"/services/{cfg['service_id']}/deploys", json={})
    say("• deploying the latest code…")
    ok = wait_live(cfg["service_id"]) and healthy(cfg)
    say("✓ up to date" if ok else "✗ the deploy didn't come up — see logs")


def status() -> None:
    cfg = load()
    if not cfg.get("service_id"):
        fail("not set up yet")
    deploys = render("GET", f"/services/{cfg['service_id']}/deploys?limit=1")
    d = deploys[0]["deploy"] if deploys else {}
    say(f"server: {cfg['url']}  (linked: {'yes' if cfg.get('enabled') else 'no'})")
    say(f"last deploy: {d.get('status')} · {(d.get('commit') or {}).get('message', '')[:60]!r} "
        f"· {d.get('finishedAt') or d.get('createdAt')}")
    try:
        say(f"health: {requests.get(cfg['url'] + '/health', timeout=60).text}")
    except requests.RequestException as exc:
        say(f"health: unreachable ({type(exc).__name__})")


def logs() -> None:
    cfg = load()
    if not cfg.get("service_id"):
        fail("not set up yet")
    res = render("GET", f"/logs?ownerId={cfg['owner_id']}&resource={cfg['service_id']}"
                        f"&limit=100&direction=backward")
    for row in reversed(res.get("logs") or []):
        say(f"{row.get('timestamp', '')[:19]}  {row.get('message', '')}")


def pull() -> None:
    """The server's memory back onto this PC (before hosting Vigil here again)."""
    cfg = load()
    if not cfg.get("data_repo"):
        fail("not set up yet")
    say(f"• {store(cfg).restore()}")


def home() -> None:
    pull()
    cfg = load()
    cfg["enabled"] = False
    save_config(cfg)
    say("✓ This PC hosts Vigil again once G.I.L. (or the watcher) restarts. The server keeps "
        "running until you suspend it in Render — while both run, you'd hear things twice.")


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)
    cmd = next((a for a in sys.argv[1:] if not a.startswith("--")), "setup")
    {"setup": setup, "deploy": deploy, "status": status, "logs": logs,
     "pull": pull, "home": home}.get(cmd, lambda: fail(f"unknown command {cmd}"))()
