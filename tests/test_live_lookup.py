"""Live lookups: research's Groq browser_search provider, and the fast path
that sends current-fact questions to it. No network — Groq is faked.

The bug these guard: asked for the weather in Tel Aviv, the brain answered
"28°C" from nowhere (it was 23.5); asked for Bitcoin, it quoted a years-old
price. Current facts must come from a search, never from model memory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import research
import fast_path_handler as fp


class _Resp:
    def __init__(self, body, status=200):
        self._body, self.status_code = body, status

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


def _groq_body(content, results):
    return {"model": "openai/gpt-oss-20b",
            "usage": {"total_tokens": 2400, "prompt_tokens": 2000, "completion_tokens": 400},
            "choices": [{"message": {"content": content, "executed_tools": [
                {"type": "browser_search", "search_results": {"results": results}}]}}]}


def test_live_search_returns_the_answer_with_its_sources(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "k")
    body = _groq_body("Bitcoin is **$77,786.87**【1†L1-L5】 as of Oct 30.",
                      [{"title": "Bitcoin price today", "url": "https://coinmarketcap.com/btc"}])
    monkeypatch.setattr(research.requests, "post", lambda *a, **k: _Resp(body))
    out = research._search_live("price of bitcoin right now", 5)
    assert len(out) == 1
    assert "77,786.87" in out[0]["text"] and "【" not in out[0]["text"]
    assert "coinmarketcap.com" in out[0]["text"] and out[0]["url"].startswith("https://")


def test_an_answer_that_never_searched_is_not_a_live_result(monkeypatch):
    # the exact failure: the model answers from memory with no search behind it
    monkeypatch.setenv("GROQ_API_KEY", "k")
    body = _groq_body("Bitcoin is trading at $27,450.", [])
    monkeypatch.setattr(research.requests, "post", lambda *a, **k: _Resp(body))
    assert research._search_live("price of bitcoin", 5) == []


def test_rate_limited_key_moves_to_the_next(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "a")
    monkeypatch.setenv("GROQ_API_KEY_2", "b")
    calls = []

    def post(*a, **k):
        calls.append(k["headers"]["Authorization"])
        if len(calls) == 1:
            return _Resp({}, 429)
        return _Resp(_groq_body("Sunny, 24°C.", [{"title": "wx", "url": "https://wx.example"}]))
    monkeypatch.setattr(research.requests, "post", post)
    assert research._search_live("weather tel aviv", 5)
    assert calls == ["Bearer a", "Bearer b"]


def test_a_live_answer_is_not_padded_with_older_articles(monkeypatch):
    live = [{"title": "Live web search", "url": "https://x", "snippet": "s", "text": "t"}]
    monkeypatch.setattr(research, "_search_live", lambda q, n: live)

    def must_not_run(q, n):
        raise AssertionError("news engines ran after a live answer")
    for name in ("_search_tavily", "_search_google_news", "_search_wikipedia"):
        monkeypatch.setattr(research, name, must_not_run)
    assert research._search("bitcoin price", 6) == live


def test_without_a_live_answer_the_other_engines_still_run(monkeypatch):
    monkeypatch.setattr(research, "_search_live", lambda q, n: [])
    monkeypatch.setattr(research, "_search_tavily", lambda q, n: [])
    news = [{"title": "n", "url": "https://news", "snippet": "x"}]
    monkeypatch.setattr(research, "_search_google_news", lambda q, n: news)
    monkeypatch.setattr(research, "_search_wikipedia", lambda q, n: [])
    assert research._search("anything", 6) == news


def test_live_results_are_read_from_their_text_not_refetched(monkeypatch):
    live = [{"title": "Live web search", "url": "https://coinmarketcap.com/btc",
             "snippet": "s", "text": "Bitcoin is $77,786.87."}]
    monkeypatch.setattr(research, "_search", lambda q, n=6: live)

    def no_fetch(url, cap=3500):
        raise AssertionError("fetched a page whose answer was already in hand")
    monkeypatch.setattr(research, "_fetch_text", no_fetch)
    corpus, used = research._gather("bitcoin price")
    assert "77,786.87" in corpus[0] and used == live


# ── the fast path that decides "this needs a search" ─────────────────────────

def _routes_live(text):
    lower = text.lower()
    return (fp._looks_like_question(text, lower)
            and not any(w in lower for w in fp._LIVE_EXCLUDE)
            and any(s in lower for s in fp._LIVE_SIGNALS))


def test_current_fact_questions_go_to_live_research():
    for q in ("What's the weather in Tel Aviv today?",
              "what's the weather in tel aviv today",          # spoken: no "?"
              "What is the price of Bitcoin right now?",
              "what are the top headlines",
              "מה מזג האוויר בתל אביב היום",                     # Hebrew, spoken
              "כמה עולה ביטקוין עכשיו",
              "מה החדשות"):
        assert _routes_live(q), q


def test_statements_and_tasks_do_not_trigger_a_search():
    for q in ("build a weather app in python",
              "remind me to check the weather at 8",
              "I read the news this morning",
              "open the weather website"):
        assert not _routes_live(q), q
