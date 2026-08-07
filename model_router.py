"""
model_router.py — Auto mode: pick the brain that fits the message.

When Auto is off, GIL uses whatever the model picker says, always. When it is
on, every prompt is classified and answered by the model best suited to it.

Why it is built the way it is, from measurements on this account:

  * The whole time-to-first-token spread between the fastest model here and the
    strongest is about 195ms. There is no slow model to route away from, so
    this routes for CAPABILITY and treats speed as a tiebreaker.
  * Asking a small model to classify costs ~127ms — two thirds of that spread.
    So classification never blocks a reply: tier 0 and tier 1 are local and
    effectively free, and anything they cannot decide falls back to a good
    default rather than paying for a verdict.
  * A hand-written keyword classifier scored 100% on the examples it was
    written against and 35% on held-out phrasing. Every tier here therefore
    ABSTAINS when unsure instead of guessing, and tier 1 is a statistical
    model over many weak features rather than a list of rules.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).parent
_LOG = ROOT / "data" / "route_log.jsonl"


# ── lanes ─────────────────────────────────────────────────────────────────────
class Lane:
    FAST = "fast"
    BALANCED = "balanced"
    REASON = "reason"
    LONGFORM = "longform"
    VISION = "vision"
    TOOLS = "tools"


#: Which model serves each lane, and how it is described to the user.
#: Latencies were measured on this account (median of 3, streaming).
LANES = {
    Lane.FAST: {
        "model": "llama-3.1-8b-instant", "label": "Quick answer",
        "hue": "#4ED8C0", "ttft": 401, "rate": 864,
        "why": "Fastest sustained output by a wide margin — right when the "
               "answer is short and quality headroom is irrelevant.",
    },
    Lane.BALANCED: {
        "model": "openai/gpt-oss-20b", "label": "Balanced",
        "hue": "#8FB8FF", "ttft": 467, "rate": 801,
        "why": "Within 66ms of the fastest first token at 93% of its "
               "throughput, from a far more capable family. Carries most turns.",
    },
    Lane.REASON: {
        "model": "openai/gpt-oss-120b", "label": "Deep reasoning",
        "hue": "#A48BFF", "ttft": 590, "rate": 477,
        "why": "The strongest brain available here, and only 190ms behind the "
               "fastest to first token.",
    },
    Lane.LONGFORM: {
        "model": "llama-3.3-70b-versatile", "label": "Long-form",
        "hue": "#F0A868", "ttft": 527, "rate": 585,
        "why": "A genuinely different prose voice from the gpt-oss family.",
    },
    Lane.VISION: {
        "model": "qwen/qwen3.6-27b", "label": "Reading the image",
        "hue": "#6FCFF0", "ttft": 395, "rate": 472,
        "why": "The only model here that reads images. Also the fastest first "
               "token on the account.",
    },
    Lane.TOOLS: {
        "model": "groq/compound-mini", "label": "Looking it up",
        "hue": "#7BD88F", "ttft": 798, "rate": 440,
        "why": "Searches the web and runs code itself, replacing a multi-step "
               "agent loop — so it wins end to end despite the slowest start.",
    },
}
#: groq/compound is deliberately ABSENT. It returned no content in streaming
#: mode across three attempts; a lane that silently answers nothing is worse
#: than a missing lane. Re-add once its tool-step response shape is handled.

DEFAULT_LANE = Lane.BALANCED


def model_for(lane: str) -> str:
    return LANES.get(lane, LANES[DEFAULT_LANE])["model"]


# ── tier 0: facts, never guesses ──────────────────────────────────────────────
_FENCE = re.compile(r"```|^\s*(def |class |function |const |import |#include)",
                    re.M)
_TRACE = re.compile(r"Traceback \(most recent|^\s+at [\w.$]+\(|"
                    r"^\w+Error:|^\w+Exception:", re.M)
_THINK_HARD = re.compile(r"\b(think (hard|carefully|step by step)|"
                         r"take your time|be thorough|reason through)\b", re.I)

#: Asking for code to be WRITTEN, as opposed to debugged. This was the gap that
#: sent "write me code" to the small fast model and "write a python function"
#: to the long-form one — because the corpus had plenty of debugging examples
#: and almost none of these, while "write" is a strong long-form signal.
_CODE_REQ = re.compile(r"""
    \b(write|make|build|create|generate|give\s+me|need|want|do|help\s+me\s+with|
       help\s+with|show\s+me)\b [^.?!]{0,40}?
      \b(code|script|function|program|app|class|method|query|regex|
         algorithm|component|endpoint|module|test|tests|snippet)\b
  | \b(code|script|program)\s+(this|that|it|something|me)\b
  | \b(in|using|with)\s+(python|javascript|typescript|java|c\+\+|rust|go|sql|
      bash|powershell|html|css|react)\b
  | \b(fix|debug|refactor|optimi[sz]e|rewrite)\b [^.?!]{0,30}?
      \b(code|script|function|bug|error|class|query)\b
""", re.I | re.X)
#: Must express INTENT to look something up, never just contain a word that
#: also means something else. A bare "search" sent "why does my binary search
#: overflow" to the web-search model — in the tier whose whole job is to be
#: never wrong. Same trap for "current" (current value), "latest" (latest
#: commit) and "as of" (as of this version).
_LOOKUP = re.compile(r"""
    \b(search|look)\s+(the\s+web|online|the\s+internet|it\s+up|this\s+up|that\s+up)\b
  | \bgoogle\s+(it|that|this|for)\b
  | \b(latest|current|today'?s|this\s+week'?s)\s+
      (news|headlines|price|prices|score|scores|weather|forecast|results|
       release|version|rate|rates)\b
  | \bnews\s+(about|on)\b
  | \bwhat'?s\s+happening\b
  | \b(right\s+now|as\s+of\s+(today|now))\b
  | \bwho\s+won\s+the\b
""", re.I | re.X)

#: above this many words the prompt is a document, not a question
_LONG_WORDS = 600


def tier0(prompt: str, has_image: bool = False) -> tuple[str | None, str]:
    """Deterministic signals only. Returns (lane, reason) or (None, "")."""
    t = prompt or ""
    if has_image:
        return Lane.VISION, "an image is attached"
    if _TRACE.search(t):
        return Lane.REASON, "the message contains a traceback"
    if _FENCE.search(t):
        return Lane.REASON, "the message contains code"
    if _THINK_HARD.search(t):
        return Lane.REASON, "you asked for careful reasoning"
    if _CODE_REQ.search(t):
        return Lane.REASON, "this is a coding job"
    if len(t.split()) > _LONG_WORDS:
        return Lane.LONGFORM, "the message is document-length"
    if _LOOKUP.search(t):
        return Lane.TOOLS, "this needs current information"
    return None, ""


# ── tier 1: a statistical classifier, trained below ───────────────────────────
_TOKEN = re.compile(r"[a-z']+")


def _features(text: str) -> Counter:
    """Words plus adjacent word pairs, so phrasing matters but no single word
    decides. Character-level noise (case, punctuation) is discarded."""
    words = _TOKEN.findall((text or "").lower())[:60]
    f = Counter(words)
    f.update(f"{a}_{b}" for a, b in zip(words, words[1:]))
    if len(words) <= 4:
        f["<<short>>"] += 2
    elif len(words) >= 30:
        f["<<long>>"] += 2
    if (text or "").strip().endswith("?"):
        f["<<question>>"] += 1
    return f


class Centroid:
    """Nearest-centroid over TF-IDF, in pure numpy.

    Chosen because it generalises from few examples, trains in milliseconds,
    needs no dependency this project does not already have, and — critically —
    reports a MARGIN, so it can decline to answer. A classifier that must
    always pick is how the regex version reached 35% on real phrasing."""

    def __init__(self):
        self.idf: dict[str, float] = {}
        self.centroids: dict[str, dict[str, float]] = {}
        self.trained = False

    def fit(self, samples: list[tuple[str, str]]):
        docs = [(_features(t), lane) for t, lane in samples]
        n = len(docs)
        df = Counter()
        for f, _ in docs:
            df.update(f.keys())
        self.idf = {w: math.log((n + 1) / (c + 1)) + 1.0 for w, c in df.items()}

        sums: dict[str, Counter] = {}
        counts: Counter = Counter()
        for f, lane in docs:
            v = self._vec(f)
            s = sums.setdefault(lane, Counter())
            for w, x in v.items():
                s[w] += x
            counts[lane] += 1
        self.centroids = {}
        for lane, s in sums.items():
            c = {w: x / counts[lane] for w, x in s.items()}
            self.centroids[lane] = self._norm(c)
        self.trained = True

    def _vec(self, f: Counter) -> dict:
        v = {w: (1 + math.log(c)) * self.idf.get(w, 1.0) for w, c in f.items()}
        return self._norm(v)

    @staticmethod
    def _norm(v: dict) -> dict:
        mag = math.sqrt(sum(x * x for x in v.values())) or 1.0
        return {w: x / mag for w, x in v.items()}

    def predict(self, text: str) -> tuple[str | None, float]:
        """(lane, margin). Margin is best minus runner-up: how much better the
        winner is, not how confident it looks on its own."""
        if not self.trained:
            return None, 0.0
        v = self._vec(_features(text))
        scored = sorted(
            ((sum(v.get(w, 0.0) * x for w, x in c.items()), lane)
             for lane, c in self.centroids.items()), reverse=True)
        if not scored:
            return None, 0.0
        best, lane = scored[0]
        runner = scored[1][0] if len(scored) > 1 else 0.0
        return lane, best - runner


#: How much better the winner must be before tier 1 is trusted.
#:
#: Chosen by sweeping it against HELD-OUT phrasing in _routertest, not by eye.
#: Higher is not better: at 0.12 precision is unchanged but coverage falls to
#: 58%, so it abstains its way to a technically-perfect, useless router. 0.030
#: was the best precision-times-coverage point — 87% correct on the 83% of
#: prompts it will commit to.
CONFIDENT = 0.030

# Training corpus. Deliberately varied in phrasing — terse and verbose, polite
# and blunt, typo'd and clean — because the failure mode being designed around
# is a classifier that only recognises the way its author happens to write.
TRAIN: list[tuple[str, str]] = [
    # ── fast: short, factual, transactional ──
    ("hey", Lane.FAST), ("morning", Lane.FAST), ("thanks!", Lane.FAST),
    ("nah forget it", Lane.FAST), ("cool thanks", Lane.FAST),
    ("what time is it in tokyo", Lane.FAST), ("whats the date", Lane.FAST),
    ("open spotify", Lane.FAST), ("mute the tv", Lane.FAST),
    ("turn the lights down", Lane.FAST), ("play something upbeat", Lane.FAST),
    ("whats on my calendar", Lane.FAST), ("is it raining", Lane.FAST),
    ("who is ada lovelace", Lane.FAST), ("capital of peru", Lane.FAST),
    ("define entropy", Lane.FAST), ("what does api stand for", Lane.FAST),
    ("how many bytes in a gigabyte", Lane.FAST),
    ("remind me what a monad is", Lane.FAST),
    ("whats my battery at", Lane.FAST), ("volume to 40", Lane.FAST),
    ("close chrome", Lane.FAST), ("what day is it", Lane.FAST),
    ("spell accommodate", Lane.FAST), ("how tall is everest", Lane.FAST),
    ("who won the game", Lane.FAST), ("set a timer for ten minutes", Lane.FAST),
    ("whats 20 percent as a fraction", Lane.FAST),
    ("say that in hebrew", Lane.FAST), ("nevermind", Lane.FAST),

    # ── reason: code, maths, debugging, analysis ──
    ("why is my recursive fib so slow", Lane.REASON),
    ("prove that sqrt 2 is irrational", Lane.REASON),
    ("whats the time complexity of quicksort", Lane.REASON),
    ("refactor this class to use dependency injection", Lane.REASON),
    ("solve for x 3x squared plus 2x minus 5", Lane.REASON),
    ("my unit test is failing and i cant see why", Lane.REASON),
    ("write a regex that matches balanced parens", Lane.REASON),
    ("theres a race condition somewhere in this code", Lane.REASON),
    ("how does this algorithm break on empty input", Lane.REASON),
    ("any idea what that error means", Lane.REASON),
    ("whats 15 percent of 240", Lane.REASON),
    ("i think theres an off by one somewhere", Lane.REASON),
    ("this keeps returning none and i dont know why", Lane.REASON),
    ("figure out the big o of this loop", Lane.REASON),
    ("is 91 prime", Lane.REASON),
    ("trace through this and tell me where it goes wrong", Lane.REASON),
    ("why does this deadlock", Lane.REASON),
    ("optimise this query its taking eight seconds", Lane.REASON),
    ("whats wrong with my sql join", Lane.REASON),
    ("explain why this returns the wrong sign", Lane.REASON),
    ("how do i make this thread safe", Lane.REASON),
    ("derive the formula for compound interest", Lane.REASON),
    ("debug the login flow its rejecting valid users", Lane.REASON),
    ("what edge cases am i missing here", Lane.REASON),
    ("calculate the monthly payment on a 300k mortgage", Lane.REASON),
    ("should this be a set or a dict for performance", Lane.REASON),
    ("find the bug in my binary search", Lane.REASON),
    ("how much memory does this actually use", Lane.REASON),
    ("work out the probability of three heads in five flips", Lane.REASON),
    ("is this o n log n or o n squared", Lane.REASON),
    # asking for code to be WRITTEN, not debugged — the gap that sent
    # "write me code" to the small model and "write a python function" to
    # the long-form one
    ("write me a function that reverses a string", Lane.REASON),
    ("build me a script to rename these files", Lane.REASON),
    ("can you code something that reads a csv", Lane.REASON),
    ("make a class for handling users", Lane.REASON),
    ("generate a sql query for monthly totals", Lane.REASON),
    ("i need a python script for this", Lane.REASON),
    ("give me the code for a rest endpoint", Lane.REASON),
    ("do code", Lane.REASON),
    ("help me with code", Lane.REASON),
    ("write unit tests for this module", Lane.REASON),

    # ── longform: writing, summarising, style ──
    ("write me a short story about a lighthouse", Lane.LONGFORM),
    ("summarise this report for my boss", Lane.LONGFORM),
    ("draft an email declining the meeting politely", Lane.LONGFORM),
    ("rewrite this paragraph in a warmer tone", Lane.LONGFORM),
    ("compose a poem about winter", Lane.LONGFORM),
    ("outline a blog post on spatial computing", Lane.LONGFORM),
    ("expand on this idea in a few paragraphs", Lane.LONGFORM),
    ("write a proposal pitching this feature", Lane.LONGFORM),
    ("could you make this sound less corporate", Lane.LONGFORM),
    ("give me the gist of it", Lane.LONGFORM),
    ("can you tidy up my wording here", Lane.LONGFORM),
    ("put that into a nice paragraph for me", Lane.LONGFORM),
    ("make it longer and more dramatic", Lane.LONGFORM),
    ("say that again but simpler", Lane.LONGFORM),
    ("i need three paragraphs on renewable energy", Lane.LONGFORM),
    ("turn these notes into something readable", Lane.LONGFORM),
    ("write the readme for this project", Lane.LONGFORM),
    ("tighten this up its too wordy", Lane.LONGFORM),
    ("give me a cover letter for this job", Lane.LONGFORM),
    ("condense this into three bullet points", Lane.LONGFORM),
    ("make the intro punchier", Lane.LONGFORM),
    ("write release notes for these changes", Lane.LONGFORM),
    ("rephrase this so it doesnt sound rude", Lane.LONGFORM),
    ("draft a speech for the opening", Lane.LONGFORM),
    ("summarize the transcript i pasted", Lane.LONGFORM),
    ("explain this to a five year old", Lane.LONGFORM),
    ("give me a paragraph i can send to the team", Lane.LONGFORM),
    ("write documentation for this function", Lane.LONGFORM),
    ("make this into a story", Lane.LONGFORM),
    ("polish the ending", Lane.LONGFORM),
]

_MODEL = Centroid()
_MODEL.fit(TRAIN)


# ── the routing decision ──────────────────────────────────────────────────────
def route(prompt: str, has_image: bool = False, forced: str | None = None
          ) -> dict:
    """Decide which lane answers this prompt.

    Returns {lane, model, label, hue, reason, tier, margin}. Never raises and
    never returns nothing: an undecidable prompt gets the balanced default,
    which is a good answer rather than a guess.
    """
    t0 = time.perf_counter()
    if forced:
        lane, reason, tier, margin = forced, "you picked it", "manual", 0.0
    else:
        lane, reason = tier0(prompt, has_image)
        tier, margin = "0", 0.0
        if lane is None:
            guess, margin = _MODEL.predict(prompt or "")
            if guess and margin >= CONFIDENT:
                lane, reason, tier = guess, "how the message reads", "1"
            else:
                lane, reason, tier = DEFAULT_LANE, "no strong signal", "default"

    spec = LANES.get(lane, LANES[DEFAULT_LANE])
    return {"lane": lane, "model": spec["model"], "label": spec["label"],
            "name": model_name(spec["model"]), "hue": spec["hue"],
            "reason": reason, "tier": tier, "margin": round(margin, 4),
            "took_us": int((time.perf_counter() - t0) * 1e6)}


def model_name(model_id: str) -> str:
    """The human name for a model id — "GPT-OSS 120B", not the API string.
    A switch notice that reads "openai/gpt-oss-120b" is a log line, not a
    message to a person."""
    try:
        import gil_brain
        for m in gil_brain.MODELS:
            if m["id"] == model_id:
                return m["name"]
    except Exception:
        pass
    return (model_id or "").split("/")[-1]


def log_route(prompt: str, decision: dict) -> None:
    """Append the decision so the classifier can be improved from real use.

    Routing quality is a data problem. Without a record there is no way to
    improve the router except by guessing, which is exactly how a keyword
    version reached 35% on real phrasing."""
    try:
        _LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "t": int(time.time()), "prompt": (prompt or "")[:300],
                "lane": decision.get("lane"), "tier": decision.get("tier"),
                "margin": decision.get("margin"),
                "model": decision.get("model")}, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── the Auto toggle ───────────────────────────────────────────────────────────
def auto_enabled() -> bool:
    try:
        import gil_config
        return bool(gil_config.get("auto_model", False))
    except Exception:
        try:
            p = ROOT / "data" / "gil_config.json"
            return bool(json.loads(p.read_text(encoding="utf-8"))
                        .get("auto_model", False))
        except Exception:
            return False


def set_auto(on: bool) -> bool:
    try:
        p = ROOT / "data" / "gil_config.json"
        cfg = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        cfg["auto_model"] = bool(on)
        p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False
