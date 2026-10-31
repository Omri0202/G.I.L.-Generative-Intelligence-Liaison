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


_GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def _search_live(query: str, n: int) -> list[dict]:
    """A live web search through Groq: gpt-oss with the browser_search tool,
    run on Groq's side and free with the same key. This is what makes "price
    of Bitcoin right now" come back with today's number and its source,
    instead of the figure in a days-old news article.

    The prompt is short on purpose. Inside GIL's ~5.7K-token brain prompt the
    model skipped the tool about half the time, and prompt plus search results
    overflowed the 8K-tokens-a-minute limit. On its own like this it searched
    every time.

    Returns one result whose "text" is already the answer, so _gather doesn't
    fetch a JavaScript-heavy price page that would come back unreadable."""
    keys = [k for k in (os.getenv("GROQ_API_KEY", ""),
                        os.getenv("GROQ_API_KEY_2", "")) if k]
    today = datetime.now().strftime("%B %d, %Y")
    for key in keys:
        r = requests.post(
            _GROQ_URL, timeout=60,
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": "application/json"},
            json={"model": "openai/gpt-oss-20b", "reasoning_effort": "low",
                  "max_tokens": 1500, "tools": [{"type": "browser_search"}],
                  "messages": [
                      {"role": "system", "content":
                       f"Today is {today}. Search the web and report the facts "
                       "that answer the question: numbers, dates and names "
                       "exactly as the sources give them, in plain sentences."},
                      {"role": "user", "content": query}]})
        if r.status_code == 429:
            continue
        r.raise_for_status()
        body = r.json()
        try:
            import token_meter
            u = body.get("usage") or {}
            token_meter.record(body.get("model", "openai/gpt-oss-20b"),
                               int(u.get("total_tokens") or 0),
                               int(u.get("prompt_tokens") or 0),
                               int(u.get("completion_tokens") or 0))
        except Exception:
            pass
        msg = (body.get("choices") or [{}])[0].get("message", {})
        answer = re.sub(r"【[^】]*】", "", msg.get("content") or "").strip()
        sources = [it for tool in (msg.get("executed_tools") or [])
                   for it in ((tool.get("search_results") or {}).get("results") or [])
                   if it.get("url")]
        if not answer or not sources:
            return []        # it answered without searching: not a live result
        cited = "\n".join(f"- {s.get('title', '')[:100]} ({s['url']})"
                          for s in sources[:5])
        top = sources[0]
        title = (top.get("title") or "").strip()[:100] or re.sub(r"^www\.", "", top["url"].split("/")[2])
        return [{"title": title, "url": top["url"],
                 "snippet": answer[:400], "text": f"{answer}\n\nFound on:\n{cited}"}]
    return []


def _search(query: str, n: int = 6) -> list[dict]:
    """Live web results: Groq browser_search (live, cited) → Tavily → Google
    News RSS → Wikipedia. A live answer is enough on its own: padding it with
    news articles would hand the writer older, conflicting figures."""
    out = []
    for fn in (_search_live, _search_tavily, _search_google_news, _search_wikipedia):
        try:
            out += fn(query, n)
        except Exception as exc:
            log.warning("search via %s failed: %s", fn.__name__, exc)
        if len(out) >= n or (fn is _search_live and out):
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
    for model in ("qwen/qwen3.8-27b", "openai/gpt-oss-20b"):
        for key in keys:
            try:
                r = requests.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    json={"model": model, "messages": messages,
                          "temperature": 0.25, "max_tokens": max_tokens,
                          **({"reasoning_effort": "low"} if "gpt-oss" in model else {})},
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


_FAIL_PHRASES = (
    "does not mention", "doesn't mention", "not mentioned", "no mention",
    "does not specifically", "doesn't specifically", "no information",
    "not provide", "does not provide", "doesn't provide", "unclear what",
    "could not find", "couldn't find", "no specific", "not available in",
    "not found in", "material does not", "material doesn't",
    "provided material", "provided web material", "provided sources",
)


def _gather(query: str, want: int = 5) -> tuple[list, list]:
    """Search + read. Returns (corpus_blocks, used_sources). Falls back to the
    result snippet when a page URL is unreadable (Google News redirect stubs),
    so RSS descriptions still contribute real facts."""
    results = _search(query, n=want + 2)
    corpus, used = [], []
    for res in results[:want]:
        try:
            import activity
            rid = activity.start("web", f"Reading {res['url'][:70]}")
        except Exception:
            rid = None
        body = res.get("text") or _fetch_text(res["url"])   # live results come pre-read
        if not body and res.get("snippet"):
            body = res["snippet"]        # RSS/description fallback
        if body:
            corpus.append(f"[Source: {res['title']} — {res['url']}]\n{body}")
            used.append(res)
            if rid is not None:
                activity.done(rid, f"{len(body)} chars")
        elif rid is not None:
            activity.fail(rid, "unreadable")
    return corpus, used


def _synthesize(question: str, corpus: list) -> str | None:
    today = datetime.now().strftime("%B %d, %Y")
    return _groq([
        {"role": "system", "content":
         f"You are G.I.L., a research assistant. Today is {today}. Answer the "
         "user's question DIRECTLY using ONLY the web material provided — never "
         "training memory. Lead with the direct answer, then supporting detail "
         "with concrete names, numbers and dates. If the material genuinely "
         "lacks the answer, say exactly what's missing. Do NOT invent facts. "
         "Do NOT add a sources section (appended separately). End with a one-line takeaway."},
        {"role": "user", "content":
         f"QUESTION: {question}\n\nWEB MATERIAL:\n\n" + "\n\n---\n\n".join(corpus)[:14000]},
    ])


def _answers_question(question: str, answer: str) -> bool:
    """Does the answer actually answer the question? Heuristic first (catches
    the 'the material doesn't mention X' non-answers), then a cheap LLM check."""
    low = answer.lower()
    if any(p in low for p in _FAIL_PHRASES):
        return False
    verdict = _groq([
        {"role": "system", "content":
         "You are a strict grader. Does the ANSWER directly and substantively "
         "answer the QUESTION with real specifics (not 'I couldn't find it', "
         "not about a different subject)? Reply with exactly YES or NO."},
        {"role": "user", "content": f"QUESTION: {question}\n\nANSWER: {answer[:1500]}"},
    ], max_tokens=4)
    return bool(verdict) and verdict.strip().upper().startswith("YES")


def _refine_query(question: str, prev_answer: str, round_i: int) -> str:
    """Sharpen the search when the last round didn't answer the question."""
    q = _groq([
        {"role": "system", "content":
         "The previous web search did not answer the user's question. Write a "
         "BETTER, more specific web search query (just the query text, no "
         "quotes, no explanation) that will surface the exact answer. Keep the "
         "key entity/subject front and center."},
        {"role": "user", "content":
         f"QUESTION: {question}\nWHY IT FAILED: {prev_answer[:400]}"},
    ], max_tokens=40)
    if q and 3 <= len(q.strip()) <= 160:
        return q.strip().strip('"')
    # heuristic fallback: force the subject + concrete terms
    extra = ["", "latest news", "official announcement confirmed"][min(round_i, 2)]
    return f"{question} {extra}".strip()


def run(question: str, max_rounds: int = 3) -> tuple[str, str]:
    """Research until the question is actually answered. Each round searches,
    reads sources, synthesizes, then VERIFIES the answer answers the question;
    if not, it refines the query and researches again (up to max_rounds)."""
    try:
        import activity
        aid = activity.start("search", f"Researching: {question[:60]}")
    except Exception:
        aid = None

    query = question
    all_used: list = []
    best_answer = None

    for rnd in range(1, max_rounds + 1):
        try:
            import activity
            activity.instant("brain",
                             f"Research round {rnd}: {query[:50]}" if rnd > 1
                             else f"Searching: {query[:55]}")
        except Exception:
            pass

        corpus, used = _gather(query)
        all_used += used
        if not corpus:
            query = _refine_query(question, "no sources were readable", rnd)
            continue

        answer = _synthesize(question, corpus)
        if not answer:
            break
        best_answer = answer

        if _answers_question(question, answer):
            try:
                import activity
                if aid is not None:
                    activity.done(aid, f"answered in {rnd} round(s)")
            except Exception:
                pass
            break
        # not answered → refine and go again
        if rnd < max_rounds:
            log.info("round %d didn't answer — refining query", rnd)
            query = _refine_query(question, answer, rnd)
    else:
        if aid is not None:
            try:
                import activity
                activity.done(aid, "reached round limit")
            except Exception:
                pass

    if not best_answer:
        msg = ("I searched but couldn't get readable results just now — "
               "try again in a moment.")
        if aid is not None:
            try:
                import activity
                activity.fail(aid, "no answer")
            except Exception:
                pass
        return msg, msg

    # De-duplicated sources the user can check
    seen, src_lines = set(), []
    for res in all_used:
        if res["url"] in seen:
            continue
        seen.add(res["url"])
        src_lines.append(f"- [{res['title'] or res['url'][:60]}]({res['url']})")
        if len(src_lines) >= 6:
            break
    chat_md = best_answer + "\n\n**Sources:**\n" + "\n".join(src_lines)

    spoken = _groq([
        {"role": "system", "content":
         "Compress this into 2 short spoken sentences — the key finding only. No markdown."},
        {"role": "user", "content": best_answer[:2000]},
    ], max_tokens=90) or best_answer.split("\n")[0][:220]

    return chat_md, spoken
