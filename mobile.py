"""
mobile.py — GIL for mobile: pair a phone to the desktop GIL over the LAN.

GIL's chat is already a web app served by a local HTTP server. To use it from a
phone we (1) bind the server to the network, (2) gate every non-localhost request
behind a random pairing TOKEN — the chat RPC can run code and drive the desktop,
so open LAN access would be a serious hole — and (3) hand the phone the URL +
token as a QR code to scan.

This module is the pure, testable core: LAN-IP detection, token minting, and a
dependency-light QR renderer (a QR matrix from `qrcode`, drawn as inline SVG so
nothing extra has to be bundled and it works offline).
"""

from __future__ import annotations

import secrets
import socket


def lan_ip() -> str:
    """The machine's primary LAN IPv4 (the address a phone on the same Wi-Fi can
    reach), or '127.0.0.1' if offline. Uses a throwaway UDP socket — no packets
    are actually sent — to ask the OS which local address routes outward."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def make_token() -> str:
    """A short, URL-safe pairing secret."""
    return secrets.token_urlsafe(12)


def pairing_url(ip: str, port: int, token: str) -> str:
    return f"http://{ip}:{port}/?t={token}"


def qr_svg(data: str, box: int = 8, border: int = 3, dark: str = "#0B0C10",
           light: str = "#FFFFFF") -> str:
    """Render `data` as a QR code, returned as a self-contained SVG string.

    Uses the `qrcode` matrix only (pure Python — no PIL), drawing each dark
    module as one <rect>, so it needs no image codec and embeds cleanly in the
    page. Falls back to an empty string if `qrcode` isn't available."""
    try:
        import qrcode
    except Exception:
        return ""
    qr = qrcode.QRCode(box_size=1, border=0,
                       error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(data)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    n = len(matrix)
    size = (n + border * 2) * box
    rects = []
    for y, row in enumerate(matrix):
        for x, cell in enumerate(row):
            if cell:
                rects.append(
                    f'<rect x="{(x + border) * box}" y="{(y + border) * box}" '
                    f'width="{box}" height="{box}"/>')
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" '
        f'viewBox="0 0 {size} {size}" shape-rendering="crispEdges">'
        f'<rect width="{size}" height="{size}" fill="{light}"/>'
        f'<g fill="{dark}">{"".join(rects)}</g></svg>')


def is_local(host: str) -> bool:
    """True if the request came from the machine itself (the desktop window),
    which is always trusted and needs no token."""
    return host in ("127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1")


def authorized(host: str, presented_token: str, enabled: bool,
               real_token: str) -> bool:
    """The single access rule. Localhost is always allowed; a remote client is
    allowed only when mobile is paired AND it presents the matching token."""
    if is_local(host):
        return True
    if not enabled or not real_token:
        return False
    return bool(presented_token) and presented_token == real_token
