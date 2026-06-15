"""
news.py — Project G.I.L.
Top news headlines via Google News RSS (no API key required).
Uses location.py to auto-detect country.
"""

import threading
import time
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

_cache:      list  = []
_cache_time: float = 0.0
_CACHE_TTL          = 900   # 15 minutes
_lock               = threading.Lock()


def _rss_url(country_code: str = "US", lang: str = "en") -> str:
    return (
        f"https://news.google.com/rss"
        f"?hl={lang}-{country_code}&gl={country_code}&ceid={country_code}:{lang}"
    )


def _fetch_rss(url: str, max_items: int = 7) -> list[dict]:
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    r = requests.get(url, headers=headers, timeout=8)
    r.raise_for_status()
    root    = ET.fromstring(r.content)
    channel = root.find("channel")
    if channel is None:
        return []
    articles = []
    for item in channel.findall("item")[:max_items]:
        raw_title = item.findtext("title", "").strip()
        # Google News format: "Headline - Source Name"
        title  = raw_title.rsplit(" - ", 1)[0].strip() if " - " in raw_title else raw_title
        source = item.findtext("source", "").strip()
        link   = item.findtext("link", "").strip()
        pub    = item.findtext("pubDate", "").strip()
        if title:
            articles.append({"title": title, "source": source, "url": link, "published": pub})
    return articles


def get_news(force: bool = False, max_items: int = 7) -> list[dict]:
    """
    Return latest headlines for the user's country.
    Cached for 15 minutes.
    """
    global _cache, _cache_time
    with _lock:
        if not force and _cache and time.time() - _cache_time < _CACHE_TTL:
            return _cache
        try:
            from location import get_location
            loc          = get_location()
            country_code = loc.get("country_code", "US") if loc else "US"
        except Exception:
            country_code = "US"

        url = _rss_url(country_code)
        try:
            articles = _fetch_rss(url, max_items)
            if not articles:   # fallback to US English
                articles = _fetch_rss(_rss_url("US"), max_items)
            _cache      = articles
            _cache_time = time.time()
            return articles
        except Exception as exc:
            print(f"[G.I.L. NEWS] Fetch error: {exc}")
            return _cache or []


def build_news_speech(articles: list[dict], count: int = 3) -> str:
    if not articles:
        return "No news available right now."
    headlines = [a["title"] for a in articles[:count]]
    if len(headlines) == 1:
        return f"Top story: {headlines[0]}."
    joined = ". ".join(headlines)
    return f"Here are the top {len(headlines)} stories. {joined}."


def open_news_article(index: int = 0) -> str:
    """Open the Nth article from the last fetch in the browser."""
    articles = get_news()
    if not articles:
        return "No news cached — ask me for news first."
    idx = max(0, min(index, len(articles) - 1))
    url = articles[idx].get("url", "")
    if url:
        webbrowser.open(url)
        return f"Opening: {articles[idx]['title'][:60]}."
    return "No URL for that article."
