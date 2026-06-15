"""
location.py — Project G.I.L.
IP-based location, nearby suggestions, map launcher, traffic, local context.
No API key required — uses ip-api.com (free, 45 req/min).
"""

import time
import threading
import webbrowser
import urllib.parse
import requests

_cache: dict    = {}
_cache_time     = 0.0
_CACHE_TTL      = 1800   # refresh every 30 minutes
_lock           = threading.Lock()


# ── Core location ─────────────────────────────────────────────────────────────

def get_location(force: bool = False) -> dict:
    """
    Return dict with: city, region, country, lat, lon, timezone, isp.
    Cached for 30 min. Returns {} on failure.
    """
    global _cache, _cache_time
    with _lock:
        if not force and _cache and time.time() - _cache_time < _CACHE_TTL:
            return _cache
        try:
            r = requests.get(
                "http://ip-api.com/json/?fields=status,city,regionName,country,countryCode,lat,lon,timezone",
                timeout=5)
            data = r.json()
            if data.get("status") == "success":
                _cache = {
                    "city":         data.get("city", ""),
                    "region":       data.get("regionName", ""),
                    "country":      data.get("country", ""),
                    "country_code": data.get("countryCode", "US"),
                    "lat":          data.get("lat", 0),
                    "lon":          data.get("lon", 0),
                    "timezone":     data.get("timezone", ""),
                }
                _cache_time = time.time()
                return _cache
        except Exception as exc:
            print(f"[G.I.L. LOCATION] Failed: {exc}")
    return _cache or {}


def get_location_string() -> str:
    loc = get_location()
    if not loc:
        return "Unknown location"
    return f"{loc['city']}, {loc['region']}, {loc['country']}"


# ── Map launcher ──────────────────────────────────────────────────────────────

def open_nearby(query: str) -> str:
    """Open Google Maps searching for `query` near the user's location."""
    loc  = get_location()
    city = f"{loc.get('city', '')}, {loc.get('country', '')}" if loc else ""

    if loc and loc.get("lat") and loc.get("lon"):
        # Coordinate-based search — most accurate
        lat, lon = loc["lat"], loc["lon"]
        q   = urllib.parse.quote(f"{query} near me")
        url = f"https://www.google.com/maps/search/{q}/@{lat},{lon},14z"
    elif city.strip(", "):
        q   = urllib.parse.quote(f"{query} near {city}")
        url = f"https://www.google.com/maps/search/{q}"
    else:
        q   = urllib.parse.quote(query)
        url = f"https://www.google.com/maps/search/{q}"

    webbrowser.open(url)
    label = query.strip().capitalize()
    near  = f" near {loc['city']}" if loc.get("city") else ""
    return f"Map open — showing {label}{near}."


def open_directions(destination: str, mode: str = "driving") -> str:
    """Open Google Maps directions from current location to destination."""
    loc = get_location()
    if loc and loc.get("lat") and loc.get("lon"):
        origin = f"{loc['lat']},{loc['lon']}"
    else:
        origin = "My+Location"

    dest = urllib.parse.quote(destination)
    url  = (
        f"https://www.google.com/maps/dir/{urllib.parse.quote(origin)}/{dest}"
        f"/?travelmode={mode}"
    )
    webbrowser.open(url)
    return f"Directions to {destination} open."


def check_traffic(destination: str) -> str:
    """Open Google Maps live traffic view for a route."""
    return open_directions(destination, mode="driving")


# ── Proactive context ─────────────────────────────────────────────────────────

def build_location_context() -> str:
    loc = get_location()
    if not loc:
        return ""
    parts = [f"USER LOCATION: {loc['city']}, {loc['region']}, {loc['country']}"]
    if loc.get("timezone"):
        parts.append(f"Timezone: {loc['timezone']}")
    return "\n".join(parts)


# ── Wolt / food delivery ──────────────────────────────────────────────────────

def open_food_delivery(query: str = "") -> str:
    """Open Wolt (popular in Israel/EU) for the user's city."""
    loc  = get_location()
    city = loc.get("city", "").lower().replace(" ", "-") if loc else ""
    if city:
        url = f"https://wolt.com/en/isr/{city}"
    else:
        url = "https://wolt.com"
    webbrowser.open(url)
    return "Wolt open — what are you ordering?"


# ── Suggestion engine ─────────────────────────────────────────────────────────

_SUGGESTIONS = {
    "morning":   ["coffee shop", "gym", "park"],
    "afternoon": ["restaurant", "café", "shopping"],
    "evening":   ["restaurant", "bar", "live music venue", "cinema"],
    "night":     ["late night food", "bar", "bowling alley"],
}

def get_suggestions(time_of_day: str = "") -> list[str]:
    """Return a list of activity categories appropriate for the time of day."""
    from datetime import datetime
    if not time_of_day:
        h = datetime.now().hour
        if 5 <= h < 12:
            time_of_day = "morning"
        elif 12 <= h < 17:
            time_of_day = "afternoon"
        elif 17 <= h < 22:
            time_of_day = "evening"
        else:
            time_of_day = "night"
    return _SUGGESTIONS.get(time_of_day, _SUGGESTIONS["afternoon"])
