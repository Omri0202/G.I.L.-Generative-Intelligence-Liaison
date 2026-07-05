# G.I.L. Upgrade Roadmap — Full Audit vs. State of the Art

*Generated 2026-07-02 (v2.5.0). Compared against: ChatGPT desktop + Realtime Voice, Claude (Code/Desktop/MCP), Microsoft Copilot (Windows), Gemini Live, and the leading open-source agent stacks (Pipecat, LiveKit Agents, Open Interpreter, Ollama-based assistants).*

**Legend:** 🔥 = highest impact · Effort: S (hours) / M (days) / L (week+)

---

## 1. Brain & Intelligence

**Today:** Groq Llama 3.1-8B/3.3-70B with smart routing, single-turn JSON action responses, mission planning, sub-agent prompt modes, 40-message rolling history.

**Gap:** Every top assistant streams tokens and runs *multi-turn* tool loops (act → observe → decide). GIL's brain fires one JSON and stops — only the code agent iterates. Competitors also run frontier models (GPT-5-class, Claude, Gemini 1M-token context).

| Upgrade | Impact | Effort |
|---|---|---|
| 🔥 **Frontier brain option** — you already have `ANTHROPIC_API_KEY` for webgen; let the *brain* use Claude (Haiku for speed, Sonnet for complex) when the key exists, Groq as fallback | Massive quality jump on judgment, Hebrew, planning | S |
| 🔥 **General tool-feedback loop** — any action's result goes back to the brain for a grounded follow-up (today only missions/dev tools do this) | GIL stops guessing what happened | M |
| **Token streaming** — Groq/Anthropic SSE → chat renders words as they arrive (current "streaming" is animation after the full reply) | Feels 3× faster | M |
| **Clarify-when-ambiguous** — brain returns `"clarify"` action when confidence is low, instead of guessing wrong | Fewer wrong actions | S |
| **Context budget manager** — summarize old history into a running digest instead of hard-truncating at 40 messages | Long conversations stop losing the thread | M |

## 2. Voice Pipeline

**Today:** speech_recognition + edge-tts, wake phrase, echo/similarity filters, addressing gate (v2.5.0).

**Gap:** ChatGPT Realtime and Gemini Live are full-duplex: ~300ms round-trip, interruptible mid-sentence, emotional prosody. Open source reaches 150ms TTS (CosyVoice2) and streaming Whisper.

| Upgrade | Impact | Effort |
|---|---|---|
| 🔥 **Barge-in** — user speaking cancels GIL's TTS instantly (mute logic exists; needs VAD-triggered stop) | Conversations stop feeling like walkie-talkie | M |
| 🔥 **Local streaming STT** — faster-whisper (small/medium) locally: lower latency, offline, better Hebrew than the current recognizer | Speed + privacy + Hebrew | M |
| **Speaker verification** — enroll Omri's voice fingerprint; commands from other voices ignored (completes the v2.5.0 "not talking to me" fix at the audio level) | Security + precision | M |
| **Piper/Kokoro local TTS fallback** — works offline, sub-200ms | Resilience | S |
| **Prosody-aware speech** — SSML emphasis on edge-tts (numbers, names) | Sounds alive | S |

## 3. Vision & Screen Awareness

**Today:** camera window, face ID, gestures, `context_engine` window-title/screen text context.

**Gap:** Gemini Live does continuous screen sharing with commentary; Copilot Vision sees any app; Claude computer-use clicks things.

| Upgrade | Impact | Effort |
|---|---|---|
| 🔥 **VLM screen understanding** — hotkey/voice "look at my screen": screenshot → Groq Llama-4-Scout (or Claude) → real understanding of errors/UI, feeding `fix_error` with pixels not just titles | GIL debugs what he actually *sees* | M |
| **Watch mode** — "tell me when the render finishes": periodic screenshot diff + VLM check | True JARVIS moment | M |
| **Camera scene memory** — remember faces/objects seen ("where did I leave my keys" from past frames) | Wow factor | L |

## 4. Memory & Personalization

**Today:** user_profile.json, keyword memory extraction, preferences, session memory, SQLite chat history.

**Gap:** ChatGPT Memory and Gemini Personalization retrieve semantically. GIL's recall is keyword-based.

| Upgrade | Impact | Effort |
|---|---|---|
| 🔥 **Vector RAG over everything** — embed chat history + memories (sqlite-vec + a local embedding model or Groq); brain context becomes "the 5 most relevant past moments" | Real long-term memory | M |
| **Memory dashboard** — settings tab listing what GIL remembers, with delete/edit | Trust + control | M |
| **Daily reflection** — nightly job distills the day's conversations into durable facts (dedup, decay old ones) | Memory stays clean | S |

## 5. Agency — Missions & Code Agent

**Today:** mission runner with live checklist, code agent with per-change approval, error fixer, activity feed.

**Gap:** Claude Code has checkpoints/rewind, plan mode, subagents, hooks, MCP. Copilot/ChatGPT agents schedule and run in background.

| Upgrade | Impact | Effort |
|---|---|---|
| 🔥 **MCP client** — speak Model Context Protocol; instantly inherit thousands of ready tool servers (Notion, Slack, browsers, databases) instead of hand-writing each integration | Ecosystem unlock — biggest single upgrade available | L |
| 🔥 **Agent checkpoints** — `git stash`-style snapshot before the code agent's first edit; "undo everything" command | Removes the biggest risk of agentic edits | S |
| **Plan-then-execute mode** — for big tasks the agent posts a numbered plan for one approval, then runs it (fewer interruptions than per-change approval; user picks the mode) | Smoother long tasks | M |
| **Scheduled missions** — "every morning at 8, run my briefing and check CI": cron store + mission runner | ChatGPT-Tasks parity | M |
| **Browser tool** — Playwright-driven browsing for the agent (read pages, fill forms with approval) | Web tasks unlocked | L |

## 6. Creative Suite (websites, images, 3D)

**Today:** multi-page intent-aware webgen + Design Studio customizer, FLUX images, 3D studio.

**Gap:** v0/Lovable/Bolt iterate conversationally on a site; ChatGPT edits images (inpainting).

| Upgrade | Impact | Effort |
|---|---|---|
| 🔥 **Conversational site editing** — "make the hero darker, rename Services to Menu": load the existing HTML, targeted LLM patch, refresh browser | Turns webgen into a real product | M |
| **One-click publish** — push generated site to GitHub Pages (gh CLI already authenticated) and speak the public URL | From toy to shipped | S |
| **Image edit/variations** — "same but at night": reuse seed + modified prompt; region inpaint via Pollinations params | Creative loop | S |
| **Brand kit** — user's saved colors/fonts/logo automatically applied to every site/image | Personalization | M |

## 7. Chat & UI

**Today (post-v2.4):** calm slate theme, task-kind activity cards with progress ribbon, browser-mockup site cards, markdown, code blocks, branching, slash commands.

| Upgrade | Impact | Effort |
|---|---|---|
| **Artifacts side panel** — code/websites open in a right-hand panel with live preview (like Claude artifacts) instead of inline blocks | Pro feel | L |
| **In-chat voice button** — push-to-talk mic in the chat input | Bridges voice/text | S |
| **Drag-in PDF/doc chat** — extract text (pypdf), RAG it, answer questions about the file | Table-stakes feature competitors have | M |
| **Global search** — search across all conversations (SQLite FTS5) | Findability | S |
| **Onboarding tour** — first-run walkthrough of missions/agent/customizer | New-user experience | M |

## 8. Integrations

**Today:** Spotify, LG TV, calendar, Gmail/WhatsApp recaps, git/docker/GitHub, PC control, weather/news/maps.

**Priority additions:** MCP (covers most of the rest) → Telegram/WhatsApp *sending* with confirm → Home Assistant bridge (lights/AC, not just TV) → phone notifications relay (KDE Connect protocol).

## 9. Proactivity

**Today:** reminders, briefing, watcher, meeting detector, proactive queries.

**Upgrades:** digest-style notifications (batch non-urgent into the next interaction instead of interrupting) — S; context triggers ("when VS Code opens, offer to resume the project") — M; end-of-day summary ("you worked 6h on GIL, 3 releases shipped") from the activity feed history — S.

## 10. Infrastructure & Quality

**Today:** PyInstaller ~150MB zip, manual release chain, no tests, no CI, plaintext credentials.

| Upgrade | Impact | Effort |
|---|---|---|
| 🔥 **CI auto-release** — GitHub Actions: push tag → build exe → attach to release (kills the 10-min local build wait every version) | Development velocity | M |
| 🔥 **Test suite** — pytest for the pure-logic modules (activity, missions, error_fixer, code_agent tools, webgen classify/fix) — most already have dry-run harnesses from this session | Stops regressions | M |
| **Credential encryption** — Windows DPAPI (`win32crypt`) for stored passwords/tokens | Security | S |
| **Crash sentinel** — GIL detects his own previous crash on startup, offers the log to the error fixer | Self-healing | S |
| **Delta updates** — download only changed files instead of 150MB | Update UX | L |

---

## Top 10 — recommended order

| # | Upgrade | Why first |
|---|---|---|
| 1 | Frontier brain option (Claude API for the brain) | One key, massive IQ jump, hours of work |
| 2 | Agent checkpoints (undo) | Safety for the feature you use most |
| 3 | Barge-in + local streaming STT | Voice is GIL's identity; this is the biggest feel gap |
| 4 | General tool-feedback loop | GIL stops being wrong about what he did |
| 5 | Vector RAG memory | Real "he remembers me" |
| 6 | CI auto-release + tests | Every future upgrade ships faster and safer |
| 7 | Conversational site editing + one-click publish | Completes the website story |
| 8 | VLM screen understanding | Supercharges error fixing and "look at this" |
| 9 | Token streaming in chat | Perceived speed |
| 10 | MCP client | Biggest long-term unlock (schedule as a milestone) |

*Sources: [IntuitionLabs enterprise comparison](https://intuitionlabs.ai/articles/claude-vs-chatgpt-vs-copilot-vs-gemini-enterprise-comparison), [DataStudios voice comparison](https://www.datastudios.org/post/voice-function-in-ai-chatbots-comparison-of-chatgpt-meta-ai-gemini-microsoft-copilot-and-claude), [Pipecat](https://github.com/pipecat-ai/pipecat), [LiveKit Agents](https://github.com/livekit/agents), [SiliconFlow voice model guide](https://www.siliconflow.com/articles/en/best-open-source-AI-models-for-voice-assistants), [Vellum open-source assistants](https://www.vellum.ai/blog/best-open-source-personal-ai-assistants).*
