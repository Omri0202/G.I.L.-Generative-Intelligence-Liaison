# "Couldn't reach my AI service" — Elimination Plan

*Goal: GIL ALWAYS answers. 2026-07-06.*

## Likely root cause (why it happens "after a few messages")
Every query sends a huge payload: ~3k-token system prompt (60+ actions, examples, memory/screen/calendar blocks) + up to 40 history messages **stored as full raw JSON replies**. Groq's free tier limits *tokens per minute*, so message 1 works, messages 3-5 exhaust the minute's budget → 429 on both keys → fallback models also fail (gemma2's 8k context can't even fit the payload → 400) → generic "couldn't reach" message. It's almost certainly **quota/size, not connectivity**.

## Step 1 — Prove it (1 hour)
- Log per request: payload tokens, model, HTTP status, error class → `data/brain_health.log`.
- Add `/health` chat command showing last 20 requests + failure reasons.
- Reproduce: send 10 rapid messages, read the log. **No fix ships until the log names the culprit.**

## Step 2 — Shrink every request (biggest win)
- Store history as **speech-only**, not raw JSON (cuts ~40%).
- Cap history at 12 messages + one-line digest of older turns.
- Slim the system prompt: remove cut features' actions (see feature audit), halve examples. Target < 1.5k tokens.
- Skip memory/recall/screen blocks for short trivial queries.

## Step 3 — Ladder that cannot end in silence
1. Anthropic (add the key — **separate quota pool**, instant 2× availability)
2. Groq key 1 → key 2 → fallback models (skip gemma2 when payload > 6k tokens)
3. On 429: **wait 20s and auto-retry silently** (don't tell the user to retry)
4. Still failing → GIL answers honestly: "Give me half a minute — I'll answer here" → background retry loop posts the real answer to chat when the API recovers. **The user never has to re-ask.**

## Step 4 — Guardrails so it never regresses
- Token budgeter: estimate payload before sending; if > 5k tokens, auto-compress history first.
- Per-minute pacing: if 3+ requests in 60s, add a 2s spacing delay.
- Health counter on the version line in chat ("0 failures today").
- CI test: simulated 429/400/timeout responses must each still produce a user-visible answer.

## Step 5 — Verify
20-message rapid-fire session + a 30-minute normal session with `/health` open: zero "couldn't reach" messages, every question answered (even if some arrive 30s late).
