# G.I.L. Feature Audit — Keep / Rework / Cut

*2026-07-06, v3.2.0. Judgment lens: what makes AIs good today is **simplicity, speed, and trust** — you ask, it answers, nothing interrupts you uninvited. Every feature below is judged by: does it serve that, and does it pay for its maintenance/size/noise?*

## KEEP — the actual product (invest here)
| Feature | Why |
|---|---|
| Brain (Claude/Groq routing) + recall + result grounding | The intelligence itself |
| Chat window + activity feed | The trust surface — seeing what GIL does |
| Voice with user on/off toggle | GIL's identity, now optional = right design |
| Code agent + error fixer + undo + approvals | The killer differentiator vs consumer AIs |
| Webgen suite (intent, multi-page, Design Studio, edit, publish) | Flagship creative feature |
| File access with permission model | Foundation for everything file-related |
| Missions | Real multi-step agency |
| Screen vision (`analyze_screen`) | Feeds the fixer; JARVIS moment |
| Reminders, weather, calendar, Spotify, PC control/volume | Cheap, daily-use utilities |

## REWORK — good idea, wrong shape
| Feature | Problem | Recommendation |
|---|---|---|
| **Startup greeting + "picking up from…"** (your example) | Uninvited speech at boot; nobody asked; causes echo/attention side-effects; session pickup is usually stale/irrelevant | **Cut the spoken greeting entirely.** Start silent, HUD shows ready. Put "last session: X" as a dismissible line in the chat welcome card instead. Opt-in config for people who want the voice hello |
| Proactive queries (proactive.py, 5-10 min timer) | Random interruptions + burns API quota for mostly nothing | Digest mode only: collect observations, mention at the *next* user interaction |
| Follow-up suggestion chips | Extra LLM call after **every** reply (latency + quota) for occasional value | Only generate for question-like replies, or cut |
| Memory stack (memory, preferences, goal_tracker, session_manager, session_memory, seed_memory = 6 modules) | Six overlapping systems, none great; heavy prompt bloat every query | Consolidate into 2: profile + recall. Delete goal_tracker/session_manager unless you actually see value in their context lines |
| Modes (dnd/study/fun) | "Fun mode" and Pomodoro are gimmicks | Keep dnd (maps to notifications+voice off). Cut study/fun |
| HUD wave visualizer (always-on-top, full screen width) | Heavy, always visible, competes with chat as the main surface | Slim to a small orb/strip; chat is the real home now |
| Watcher + triggers + meeting detector | Three background watchers, overlapping with error_watch, silent 99% of the time; meeting detector = false-positive risk | Merge into one watcher with error_watch; cut meeting detector |

## CUT (or make optional downloads) — cost exceeds value
| Feature | Cost | Verdict |
|---|---|---|
| **Camera vision + face ID + gestures** (eyes, face_id, gestures, camera_viewer ≈ 65KB code + cv2/mediapipe = the bulk of the 150MB exe) | Huge exe size, heavy deps, demo-grade daily value | Move to an optional plugin download. Default GIL without them ≈ far smaller/faster install |
| 3D studio (studio3d + viewer3d, 87KB) | Demo feature; webgen is strictly more useful | Cut or plugin |
| WhatsApp recap (browser-profile scraping) | Brittle, breaks on WhatsApp updates, privacy-sensitive | Cut; keep Gmail recap (API-based, stable) |
| Food delivery / "identify song" style one-offs | Thin URL-openers pretending to be features | Keep only if you use them; delete the rest from the prompt to shrink it |
| learning_projects | Overlaps tasks/notes; unused paths | Fold into tasks |
| Duplicate setup wizards (setup_wizard + dev_setup_wizard) | Two onboarding flows to maintain | Merge into one |
| Clip history | Windows already has Win+V | Cut |

## Why this matters beyond tidiness
1. **Prompt size = latency + cost + confusion.** Every cut action shrinks the system prompt the brain reads on *every single query* — fewer wrong-action picks by the 8B model.
2. **Exe size**: dropping cv2/mediapipe/3D likely takes the download from ~150MB to well under half.
3. **Trust**: an assistant that never speaks uninvited but always answers instantly *feels* smarter than one that greets, suggests, and interrupts.

**Suggested cut order when you're ready:** startup greeting → follow-up chips throttle → memory consolidation → camera/3D to plugins → watcher merge → prompt slimming.
