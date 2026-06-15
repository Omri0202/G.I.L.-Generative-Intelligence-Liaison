"""
spotify_control.py — Project G.I.L.
Spotify control via spotipy (full API). Plays in background — no browser or window opened.
pip install spotipy
.env: SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET
"""

import os
import re
import subprocess
import time
from pathlib import Path

_CACHE_PATH = Path(__file__).parent / "data" / ".spotify_cache"
_REDIRECT   = "http://127.0.0.1:8888/callback"   # 127.0.0.1 accepted; localhost sometimes rejected
_SCOPES     = (
    "user-read-playback-state user-modify-playback-state "
    "user-read-currently-playing playlist-read-private "
    "playlist-read-collaborative"
)

_PLAYLIST_RE = re.compile(r"\bplaylist\b", re.IGNORECASE)
_MY_RE       = re.compile(r"\bmy\b",       re.IGNORECASE)


def _get_sp():
    client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
    if not client_id:
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyPKCE   # PKCE = Spotify's required flow for desktop apps
        return spotipy.Spotify(auth_manager=SpotifyPKCE(
            client_id=client_id,
            redirect_uri=_REDIRECT,
            scope=_SCOPES,
            cache_path=str(_CACHE_PATH),
            open_browser=True,
        ))
    except Exception as exc:
        print(f"[G.I.L. SPOTIFY] Auth error: {exc}")
        return None


def _find_spotify_exe() -> Path | None:
    """Locate Spotify.exe on this PC, trying common paths then a PowerShell search."""
    candidates = [
        Path.home() / "AppData" / "Roaming" / "Spotify" / "Spotify.exe",
        Path.home() / "AppData" / "Local"   / "Spotify" / "Spotify.exe",
        # Microsoft Store install stub (works as a launcher)
        Path.home() / "AppData" / "Local" / "Microsoft" / "WindowsApps" / "Spotify.exe",
        Path("C:/Program Files/Spotify/Spotify.exe"),
        Path("C:/Program Files (x86)/Spotify/Spotify.exe"),
    ]
    for p in candidates:
        if p.exists():
            return p

    # Fallback: ask PowerShell where Spotify is
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "Get-Command spotify.exe -ErrorAction SilentlyContinue "
             "| Select-Object -ExpandProperty Source"],
            capture_output=True, text=True, timeout=6,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        found = r.stdout.strip()
        if found and Path(found).exists():
            return Path(found)
    except Exception:
        pass
    return None


def _launch_spotify_silent() -> bool:
    """Start the Spotify desktop app minimized. Returns True if launched."""
    path = _find_spotify_exe()
    if not path:
        print("[G.I.L. SPOTIFY] Spotify.exe not found — launching via URI scheme.")
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "spotify:"],
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            print("[G.I.L. SPOTIFY] Launched via spotify: URI scheme.")
            return True
        except Exception as exc:
            print(f"[G.I.L. SPOTIFY] URI launch failed: {exc}")
        return False
    try:
        si             = subprocess.STARTUPINFO()
        si.dwFlags    |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 2   # SW_SHOWMINIMIZED
        subprocess.Popen(
            [str(path), "--minimized"],
            startupinfo=si,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        print(f"[G.I.L. SPOTIFY] Launched: {path}")
        return True
    except Exception as exc:
        print(f"[G.I.L. SPOTIFY] Silent launch failed: {exc}")
        return False


def _ensure_device(sp) -> bool:
    """Return True when an active Spotify device is available; launch it silently if not."""
    try:
        if sp.devices().get("devices"):
            return True
    except Exception:
        pass

    print("[G.I.L. SPOTIFY] No active device — launching Spotify silently.")
    if not _launch_spotify_silent():
        return False

    for _ in range(15):   # wait up to 15 s for Spotify to register
        time.sleep(1)
        try:
            if sp.devices().get("devices"):
                time.sleep(2)   # extra buffer — device registered but may not be ready
                return True
        except Exception:
            pass
    return False


def _play(sp, **kwargs) -> None:
    """Call start_playback, retrying once after launching Spotify if no active device."""
    try:
        sp.start_playback(**kwargs)
        return
    except Exception as exc:
        err = str(exc)
        if "No active device" not in err and "404" not in err:
            raise
    # Device vanished or wasn't ready — launch and retry
    print("[G.I.L. SPOTIFY] Playback failed — no active device. Launching Spotify.")
    if not _launch_spotify_silent():
        raise Exception("Spotify not found on this PC.")
    for _ in range(30):
        time.sleep(1)
        try:
            devices = sp.devices().get("devices", [])
            if devices:
                time.sleep(2)
                device_id = devices[0]["id"]
                sp.start_playback(device_id=device_id, **kwargs)
                return
        except Exception as retry_exc:
            if "No active device" not in str(retry_exc) and "404" not in str(retry_exc):
                raise retry_exc
    raise Exception("Spotify launched but no active device after 30 s.")


_CALLED_RE = re.compile(r'\b(?:called|named)\s+["\']?(.+?)["\']?\s*$', re.IGNORECASE)
_FILLER_RE  = re.compile(r'\b(my|the|a|an|called|named|play|playing|and|in|on)\b', re.IGNORECASE)


def _find_user_playlist(sp, query: str):
    """Search the user's saved playlists for the best match to query."""
    # Prefer explicit "called X" / "named X" extraction — handles names that are
    # common words like "playlist" without stripping them incorrectly.
    m = _CALLED_RE.search(query)
    if m:
        search_name = m.group(1).strip().strip("'\"")
    else:
        # Remove filler but do NOT strip "playlist" — it might be the actual name
        clean = _FILLER_RE.sub("", query)
        clean = re.sub(r"\bplaylist\b", "", clean, flags=re.IGNORECASE)
        search_name = re.sub(r"\s+", " ", clean).strip()

    try:
        items = sp.current_user_playlists(limit=50).get("items", [])
        if not items:
            return None

        # No meaningful search term — return the first playlist
        if not search_name:
            return items[0]

        words = set(search_name.lower().split()) - {""}
        best, best_score = None, 0
        for pl in items:
            if not pl:
                continue
            pl_name = pl["name"].lower()
            score   = len(words & set(pl_name.split())) * 2
            if search_name.lower() in pl_name:
                score += 3
            if score > best_score:
                best_score, best = score, pl

        if best and best_score > 0:
            return best

        # Last resort: check if any playlist name appears in the original query
        for pl in items:
            if pl and pl["name"].lower() in query.lower():
                return pl

    except Exception as exc:
        print(f"[G.I.L. SPOTIFY] User playlist search error: {exc}")
    return None


def spotify_control(command: str) -> str:
    cmd = command.lower().strip()
    print(f"[G.I.L. SPOTIFY] Command received: {cmd!r}")

    # Normalise open/start/launch → play so the handlers below always match
    for prefix in ("open ", "start ", "launch ", "run "):
        if cmd.startswith(prefix):
            cmd = "play " + cmd[len(prefix):]
            break
    if cmd in ("open", "start", "launch", "run"):
        cmd = "play"

    sp = _get_sp()
    if not sp:
        return "Spotify isn't connected yet — check your SPOTIFY_CLIENT_ID in .env."

    try:
        if cmd in ("play", "resume", "start"):
            _play(sp)
            return "Playback resumed."

        if cmd in ("pause", "stop"):
            sp.pause_playback()
            return "Paused."

        if cmd in ("next", "skip", "next track", "skip track"):
            sp.next_track()
            return "Skipping."

        if cmd in ("previous", "prev", "back", "previous track", "last track"):
            sp.previous_track()
            return "Going back."

        if cmd.startswith("play "):
            query = cmd[5:].strip()
            wants_playlist = _PLAYLIST_RE.search(query) or _MY_RE.search(query)

            # ── 1. User's own playlists (e.g. "play my chill playlist") ──────
            if wants_playlist:
                pl = _find_user_playlist(sp, query)
                if pl:
                    _play(sp, context_uri=pl["uri"])
                    return f"Playing your playlist: {pl['name']}."

            # ── 2. Track search (default for song names) ──────────────────────
            if not wants_playlist:
                results = sp.search(q=query, limit=1, type="track")
                tracks  = results.get("tracks", {}).get("items", [])
                if tracks:
                    uri    = tracks[0]["uri"]
                    name   = tracks[0]["name"]
                    artists = tracks[0].get("artists", [])
                    artist  = artists[0]["name"] if artists else "Unknown"
                    _play(sp, uris=[uri])
                    return f"Playing {name} by {artist}."

            # ── 3. General playlist search (e.g. "play lofi playlist") ───────
            clean_q    = re.sub(r"\bmy\b|\bplaylist\b", "", query, flags=re.IGNORECASE).strip()
            pl_results = sp.search(q=clean_q or query, limit=5, type="playlist")
            playlists  = pl_results.get("playlists", {}).get("items", [])
            if playlists:
                pl = playlists[0]
                _play(sp, context_uri=pl["uri"])
                return f"Playing playlist: {pl['name']}."

            return f"Couldn't find '{query}' on Spotify."

        if cmd.startswith("volume") or cmd.startswith("set volume"):
            try:
                level = int(cmd.split()[-1])
                sp.volume(max(0, min(100, level)))
                return f"Spotify volume set to {level}%."
            except ValueError:
                pass

        if cmd in ("what's playing", "current track", "now playing", "what song"):
            current = sp.current_playback()
            if current and current.get("item"):
                name    = current["item"]["name"]
                artists = current["item"].get("artists", [])
                artist  = artists[0]["name"] if artists else "Unknown"
                return f"Playing {name} by {artist}."
            return "Nothing is playing right now."

        if "shuffle" in cmd:
            state = "off" not in cmd
            sp.shuffle(state)
            return f"Shuffle {'on' if state else 'off'}."

        if "repeat" in cmd:
            if "off" in cmd:
                sp.repeat("off")
                return "Repeat off."
            elif "track" in cmd or "song" in cmd:
                sp.repeat("track")
                return "Repeating this track."
            sp.repeat("context")
            return "Repeat on."

        return "Command not recognised — try 'play', 'pause', 'next', or 'play [song name]'."

    except Exception as exc:
        err = str(exc)
        print(f"[G.I.L. SPOTIFY] Command error: {err}")
        if "No active device" in err or "404" in err:
            return "Spotify has no active device. Open Spotify on your PC and try again."
        if "Premium" in err or "403" in err:
            return "Spotify Premium is required for this."
        if "token" in err.lower() or "auth" in err.lower() or "401" in err:
            return "Spotify auth expired — I'll need to reconnect. Try again."
        return f"Spotify failed: {err[:80]}"
