"""
weather.py — Project G.I.L.
Real-time weather via wttr.in — no API key required.
"""

import requests


def get_weather(location: str = "") -> str:
    try:
        loc  = location.strip().replace(" ", "+") or ""
        url  = f"https://wttr.in/{loc}?format=%C,+%t,+feels+like+%f.+Humidity:+%h.+Wind:+%w."
        resp = requests.get(url, timeout=7, headers={"User-Agent": "curl/7.68"})
        if resp.status_code == 200:
            text = resp.text.strip()
            if text and len(text) < 250 and "Unknown" not in text:
                return text
        return "Weather data unavailable right now."
    except requests.exceptions.Timeout:
        return "Weather request timed out."
    except Exception as exc:
        print(f"[G.I.L. WEATHER] {exc}")
        return "Couldn't reach the weather service."
