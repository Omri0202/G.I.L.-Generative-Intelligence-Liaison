"""
whatsapp_recap.py — Project G.I.L.
Reads unread WhatsApp messages via WhatsApp Web + Playwright.

First run: opens a browser window — scan the QR code once.
After that: runs silently in the background, no QR needed.
"""

import threading
import time
from pathlib import Path

_PROFILE_DIR  = Path(__file__).parent / "data" / "whatsapp_profile"
_SESSION_FILE = _PROFILE_DIR / ".session_ok"
_WA_URL       = "https://web.whatsapp.com"

_show_callback = None
_last_check    = 0.0
_check_lock    = threading.Lock()
_scraping      = [False]   # prevents concurrent Playwright instances


def set_show_callback(fn) -> None:
    global _show_callback
    _show_callback = fn


# ── Inner scraper (may block — always called inside a timed thread) ───────────

def _scrape_playwright() -> list[dict]:
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    already_logged_in = _SESSION_FILE.exists()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[G.I.L. WHATSAPP] playwright not installed.")
        return []

    results = []
    headless  = already_logged_in
    extra_args = (
        ["--no-sandbox", "--disable-dev-shm-usage",
         "--window-position=-32000,-32000", "--window-size=1,1"]
        if not headless else
        ["--no-sandbox", "--disable-dev-shm-usage"]
    )

    with sync_playwright() as p:
        browser = p.chromium.launch_persistent_context(
            str(_PROFILE_DIR),
            headless=headless,
            args=extra_args,
        )
        page = browser.pages[0] if browser.pages else browser.new_page()

        try:
            page.goto(_WA_URL, wait_until="domcontentloaded", timeout=20000)

            if not already_logged_in:
                print("[G.I.L. WHATSAPP] Scan the QR code in the browser window (60s)...")
                page.wait_for_selector('[data-testid="chat-list"]', timeout=90000)
                _SESSION_FILE.touch()
                print("[G.I.L. WHATSAPP] Logged in — session saved.")
            else:
                # Short timeout: if chat-list doesn't appear, session likely expired
                page.wait_for_selector('[data-testid="chat-list"]', timeout=7000)

            time.sleep(1.5)

            chats = page.query_selector_all('[data-testid="cell-frame-container"]')
            for chat in chats[:30]:
                try:
                    badge = chat.query_selector('[data-testid="icon-unread-count"]')
                    if not badge:
                        continue

                    raw   = badge.inner_text().strip()
                    count = int(raw) if raw.isdigit() else 1

                    name_el = chat.query_selector('[data-testid="cell-frame-title"]')
                    name    = name_el.inner_text().strip() if name_el else "Someone"

                    preview_el = chat.query_selector('[data-testid="cell-frame-secondary"]')
                    preview    = preview_el.inner_text().strip()[:80] if preview_el else ""
                    if preview.lower().startswith("unread message"):
                        preview = ""

                    results.append({"name": name[:30], "preview": preview, "count": count})
                except Exception:
                    continue

        except Exception as exc:
            print(f"[G.I.L. WHATSAPP] Scrape error: {exc}")
            # If the session is marked valid but chat-list never appeared,
            # the WhatsApp session has likely expired — force re-auth next time.
            if already_logged_in and "chat-list" in str(exc):
                _SESSION_FILE.unlink(missing_ok=True)
                print("[G.I.L. WHATSAPP] Session expired — will re-authenticate next time.")

        finally:
            try:
                browser.close()
            except Exception:
                pass

    return results


# ── Public API — hard timeout wrapper ────────────────────────────────────────

def get_unread_messages(timeout_secs: int = 12) -> list[dict]:
    """
    Returns list of {name, preview, count} for unread WhatsApp chats.
    Hard timeout: always returns within timeout_secs — never hangs GIL.
    """
    if _scraping[0]:
        print("[G.I.L. WHATSAPP] Scrape already in progress — skipping.")
        return []

    _scraping[0] = True
    result: list = [None]
    _generation = [object()]   # unique token so the hung thread's finally doesn't clobber a new scrape

    def _run(gen=_generation[0]):
        try:
            result[0] = _scrape_playwright()
        except Exception as exc:
            print(f"[G.I.L. WHATSAPP] Scraper thread error: {exc}")
            result[0] = []
        finally:
            if _generation[0] is gen:   # only reset if no newer scrape has started
                _scraping[0] = False

    t = threading.Thread(target=_run, daemon=True, name="GIL-WAScrape")
    t.start()
    t.join(timeout=timeout_secs)

    if result[0] is None:
        # Thread is still running (Playwright hung) — return immediately, don't block
        print(f"[G.I.L. WHATSAPP] Hard timeout after {timeout_secs}s — resuming.")
        _generation[0] = object()   # invalidate the hung thread's finally token
        _scraping[0] = False
        return []

    return result[0]


# ── Speech builder ────────────────────────────────────────────────────────────

def build_recap_speech(messages: list[dict]) -> str:
    if not messages:
        return "No unread WhatsApp messages."

    total = sum(m["count"] for m in messages)
    names = ", ".join(m["name"] for m in messages[:3])

    if len(messages) == 1:
        m      = messages[0]
        plural = f'{m["count"]} messages' if m["count"] > 1 else "a message"
        return f"You have {plural} from {m['name']}. Want me to read them to you?"

    more = f" and {len(messages) - 3} more" if len(messages) > 3 else ""
    return (
        f"You have {total} unread WhatsApp messages from {names}{more}. "
        "Want me to read them to you?"
    )


def open_whatsapp() -> None:
    import webbrowser
    webbrowser.open(_WA_URL)


# ── Proactive check ───────────────────────────────────────────────────────────

def check_and_announce(force: bool = False) -> None:
    global _last_check
    if not force and time.time() - _last_check < 1800:
        return

    with _check_lock:
        if not force and time.time() - _last_check < 1800:
            return
        _last_check = time.time()

    def _run():
        messages = get_unread_messages()
        if not messages:
            return
        speech = build_recap_speech(messages)
        print(f"[G.I.L. WHATSAPP] {len(messages)} unread chats.")
        if speech and _show_callback:
            try:
                _show_callback(speech, messages)
            except Exception as exc:
                print(f"[G.I.L. WHATSAPP] Callback error: {exc}")

    threading.Thread(target=_run, daemon=True, name="GIL-WARecap").start()


def start_periodic_check(interval_secs: int = 1800) -> None:
    def _loop():
        time.sleep(60)  # startup delay
        while True:
            check_and_announce()
            time.sleep(interval_secs)
    threading.Thread(target=_loop, daemon=True, name="GIL-WALoop").start()
    print("[G.I.L. WHATSAPP] Periodic check active (every 30 min).")
