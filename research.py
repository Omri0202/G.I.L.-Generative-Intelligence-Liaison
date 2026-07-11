"""
research.py — G.I.L. live web research with sources.

For any time-sensitive or factual question GIL does NOT answer from memory:
he searches the live web, reads the top results, writes a thorough answer
grounded ONLY in what he just read, and cites the sources so the user can
verify. Free stack (DuckDuckGo + page fetch + Groq), no API keys beyond Groq.

run(question) -> (chat_markdown_with_sources, short_spoken_summary)
"""

import os
import re
import urllib.parse
from datetime import datetime

import requests

from logger import get as _get_log

log = _get_log("research")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _clean_html(s: str) -> str:
    return re.sub(r"\s{2,}", " ", re.sub(r"<[^>]+>", "", s or "")).strip()


def _search_google_news(query: str, n: int) -> list[dict]:
    """Google News RSS — official feed, no bot-blocking, fresh by design."""
    r = requests.get(
        "https://news.google.com/rss/search",
        params={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
        timeout=10, headers={"User-Agent": _UA})
    out = []
    for item in re.findall(r"<item>([\s\S]*?)</item>", r.text)[:n]:
        def tag(name):
            m = re.search(rf"<{name}[^>]*>([\s\S]*?)</{name}>", item)
            return _clean_html(m.group(1)) if m else ""
        title, link, date = tag("title"), tag("link"), tag("pubDate")
        src = tag("source")
        # the description embeds the publisher's real article link
        dm = re.search(r'href="(https?://(?!news\.google)[^"]+)"', item)
        url = dm.group(1) if dm else link
        if title and url:
            out.append({"title": f"{title}" + (f" ({date[:16]})" if date else ""),
                        "url": url,
                        "snippet": f"{src}: {title}"})
    return out


def _search_wikipedia(query: str, n: int) -> list[dict]:
    """Wikipedia search API — background/encyclopedic grounding."""
    r = requests.get(
        "https://en.wikipedia.org/w/api.php",
        params={"action": "query", "list": "search", "srsearch": query,
                "format": "json", "srlimit": min(n, 3)},
        timeout=10, headers={"User-Agent": "GIL-assistant/4.0"})
    out = []
    for hit in r.json().get("query", {}).get("search", []):
        title = hit.get("title", "")
        out.append({
            "title": f"Wikipedia — {title}",
            "url": "https://en.wikipedia.org/wiki/" + urllib.parse.quote(title.replace(" ", "_")),
            "snippet": _clean_html(hit.get("snippet", ""))[:300]})
    return out


def _search_tavily(query: str, n: int) -> list[dict]:
    """Tavily search API — purpose-built for AI research (user has a key)."""
    key = os.getenv("TAVILY_API_KEY", "")
    if not key:
        return []
    r = requests.post("https://api.tavily.com/search",
                      json={"api_key": key, "query": query,
                            "max_results": n, "search_depth": "advanced",
                            "include_answer": False},
                      timeout=15)
    r.raise_for_status()
    return [{"title": it.get("title", "")[:120],
             "url": it.get("url", ""),
             "snippet": (it.get("content") or "")[:400]}
            for it in r.json().get("results", []) if it.get("url")]


def _search(query: str, n: int = 6) -> list[dict]:
    """Live web results: Tavily (best) → Google News RSS → Wikipedia."""
    out = []
    for fn in (_search_tavily, _search_google_news, _search_wikipedia):
        try:
            out += fn(query, n)
        except Exception as exc:
            log.warning("search via %s failed: %s", fn.__name__, exc)
        if len(out) >= n:
            break
    return out[:n + 2]


def _fetch_text(url: str, cap: int = 3500) -> str:
    """Fetch a page and reduce it to readable text."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": _UA})
        html = r.text
        # drop scripts/styles/nav junk, keep paragraph-ish text
        html = re.sub(r"<(script|style|nav|header|footer|aside)[\s\S]*?</\1>",
                      " ", html, flags=re.IGNORECASE)
        paras = re.findall(r"<(?:p|h1|h2|h3|li)[^>]*>([\s\S]*?)</(?:p|h1|h2|h3|li)>",
                           html, re.IGNORECASE)
        text = " ".join(re.sub(r"<[^>]+>", "", p).strip() for p in paras)
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text[:cap]
    except Exception:
        return ""


def _groq(messages: list, max_tokens: int = 1400) -> str | None:
    keys = [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]
    for model in ("llama-3.3-70b-versatile", "llama-3.1-8b-instant"):
        for key in keys:
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": model, "messages": messages,
                          "temperature": 0.25, "max_tokens": max_tokens},
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    timeout=30)
                if r.status_code == 429:
                    continue
                r.raise_for_status()
                return r.json()["choices"][0]["message"]["content"].strip()
            except Exception as exc:
                log.debug("groq research attempt failed: %s", exc)
    return None


def run(question: str) -> tuple[str, str]:
    """Full research pipeline. Returns (chat_markdown, spoken_summary)."""
    try:
        import activity
        aid = activity.start("search", f"Researching: {question[:60]}")
    except Exception:
        aid = None

    results = _search(question)
    if not results:
        msg = ("I couldn't reach the web to research that right now — "
               "so I won't guess. Try again in a moment.")
        if aid is not None:
            import activity
            activity.fail(aid, "no search results")
        return msg, msg

    # Read the top pages (each one visibly, like Claude does)
    corpus, used = [], []
    for res in results[:3]:
        try:
            import activity
            rid = activity.start("web", f"Reading {res['url'][:70]}")
        except Exception:
            rid = None
        body = _fetch_text(res["url"])
        if body:
            corpus.append(f"[Source: {res['title']} — {res['url']}]\n{body}")
            used.append(res)
            if rid is not None:
                activity.done(rid, f"{len(body)} chars")
        else:
            if rid is not None:
                activity.fail(rid, "unreadable")
    # snippets from remaining results as light corroboration
    for res in results[3:]:
        if res["snippet"]:
            corpus.append(f"[Source: {res['title']} — {res['url']}]\n{res['snippet']}")
            used.append(res)

    today = datetime.now().strftime("%B %d, %Y")
    answer = _groq([
        {"role": "system", "content":
         f"You are G.I.L., a research assistant. Today is {today}. Write a "
         "thorough, well-structured markdown answer to the user's question "
         "using ONLY the web material provided — never your training memory. "
         "Rules:\n"
         "- Full paragraphs + bullet points where useful; concrete names, "
         "numbers and dates from the material.\n"
         "- If sources conflict or the material doesn't fully answer, say so "
         "plainly.\n"
         "- Do NOT invent facts absent from the material.\n"
         "- Do NOT include a sources section — it is appended separately.\n"
         "- End with one short takeaway line."},
        {"role": "user", "content":
         f"QUESTION: {question}\n\nWEB MATERIAL:\n\n" + "\n\n---\n\n".join(corpus)[:14000]},
    ])

    if not answer:
        msg = "The web search worked but my summarizer is rate-limited — ask me again in a minute."
        if aid is not None:
            import activity
            activity.fail(aid, "summarizer unavailable")
        return msg, msg

    # De-duplicated sources the user can check
    seen, src_lines = set(), []
    for res in used:
        if res["url"] in seen:
            continue
        seen.add(res["url"])
        src_lines.append(f"- [{res['title'] or res['url'][:60]}]({res['url']})")
        if len(src_lines) >= 5:
            break
    chat_md = answer + "\n\n**Sources:**\n" + "\n".join(src_lines)

    spoken = _groq([
        {"role": "system", "content":
         "Compress this into 2 short spoken sentences — the key finding only. "
         "No markdown."},
        {"role": "user", "content": answer[:2000]},
    ], max_tokens=90) or answer.split("\n")[0][:220]

    if aid is not None:
        import activity
        activity.done(aid, f"{len(src_lines)} sources")
    return chat_md, spoken
