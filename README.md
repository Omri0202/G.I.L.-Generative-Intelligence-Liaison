# G.I.L. — Generative Intelligence Liaison

A desktop AI assistant for Windows that can actually operate the computer: read the
screen, run programs, edit files, manage a calendar and inbox, and hold a conversation
by voice or text.

Roughly 43,000 lines across 110 files, mostly Python.

---

## What it does

| | |
|---|---|
| **Controls the machine** | Opens and drives applications, manages windows, runs shell commands behind a safety layer |
| **Sees** | Reads the screen and any image you send it, using a vision model with automatic fallback across three providers |
| **Listens and speaks** | Wake phrase, speech recognition, and text-to-speech for hands-free use |
| **Writes and fixes code** | A coding agent that searches the project, plans an edit, applies it, and reviews its own work |
| **Signs you in by face** | Camera-based face recognition, with a password fallback |
| **Spatial workspace** | A full-screen view of the desktop navigated with hand tracking through the webcam — live window mirrors composited with DWM |
| **Connects to your accounts** | Google Calendar, Gmail, WhatsApp and Spotify, each with a live connection check rather than a remembered one |

---

## Architecture

The part I find most interesting is the agent system.

**A main agent, plus up to three sub-agents running concurrently.** Each sub-agent gets
its own engine instance and its own permission policy, held in thread-local state so a
read-only sub-agent can never inherit write access from the main one.

**Four distinct roles in the code pipeline**, each with its own system prompt:

```
plan  ──▶  execute  ──▶  review  ──▶  verify
```

The reviewer and verifier are deliberately adversarial — they are told to be strict and
independent, and they see the change without seeing the reasoning that produced it.

**Automatic model routing.** A small TF-IDF nearest-centroid classifier, written in plain
NumPy, reads each request and picks the right model for it — a fast one for short
exchanges, a stronger one for reasoning and code. When it switches, it says so in the chat
instead of changing silently.

**Multi-provider fallback for vision.** Free tiers run out. When one provider's daily
budget is exhausted the request moves to the next one rather than failing, and the image
is downscaled to a pixel budget chosen per task type.

---

## Built with

Python 3 · Groq API (`openai/gpt-oss-120b`) for the brain · OpenRouter and Google Gemini as
vision fallbacks · MediaPipe for hand tracking · OpenCV · NumPy · Win32 / DWM through
`ctypes` · CustomTkinter and a browser-based chat UI · SQLite · bcrypt · PyInstaller

---

## How this was built

The code in this repository was written with the help of
[Claude Code](https://claude.com/claude-code). I want that stated plainly and up front
rather than buried.

My own work was architecture and direction rather than typing out the code: deciding what
the system should do and how it should be structured, testing it against real use, and
diagnosing failures precisely enough that they could be fixed. A representative example —
I noticed the usage indicator was showing a per-minute limit in one place and a per-day
limit in another, and decided the per-minute number should never be shown to the user at
all, because two different percentages for the same thing is worse than one incomplete
one. The engine still tracks both.

Most of what I learned here was not syntax. It was how to break a problem into pieces, how
to describe a fault accurately enough to be actionable, and how to hold a system this size
in my head.

At school I study Java.

---

## Running it

```bash
git clone https://github.com/Omri0202/G.I.L.-Generative-Intelligence-Liaison.git
cd G.I.L.-Generative-Intelligence-Liaison
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your own API keys, then:

```bash
python main.py
```

Windows only — it depends on Win32 and DWM APIs for window control and the spatial
workspace.

---

## License

MIT — see [LICENSE](LICENSE).
