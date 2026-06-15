"""
briefing.py — Project G.I.L.
Morning briefing: time greeting + weather + unread emails + pending tasks.
"""

from datetime import datetime


def build_briefing(location: str = "") -> str:
    parts = []

    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good morning."
    elif hour < 17:
        greeting = "Good afternoon."
    else:
        greeting = "Good evening."
    parts.append(greeting)

    # Weather
    try:
        from weather import get_weather
        wx = get_weather(location)
        if wx and "unavailable" not in wx.lower() and "error" not in wx.lower():
            parts.append(f"Weather: {wx}")
    except Exception:
        pass

    # Unread Gmail
    try:
        import gmail_recap
        emails = gmail_recap.get_unread_summary(max_results=3)
        if emails:
            sender = emails[0]["sender"]
            parts.append(
                f"You have {len(emails)} unread email{'s' if len(emails) != 1 else ''} — "
                f"latest from {sender}."
            )
        else:
            parts.append("Inbox is clear.")
    except Exception:
        pass

    # Pending tasks
    try:
        from tasks import list_tasks
        tasks   = list_tasks()
        pending = [t for t in tasks if not t.get("done")]
        if pending:
            parts.append(
                f"{len(pending)} task{'s' if len(pending) != 1 else ''} pending."
            )
    except Exception:
        pass

    if len(parts) == 1:
        parts.append("No alerts — all clear.")

    return " ".join(parts)
