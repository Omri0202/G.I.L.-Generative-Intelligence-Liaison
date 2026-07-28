"""
GIL for mobile — pairing token, access rule, LAN IP, QR. Pure logic, no network.
Run: python -m pytest tests/test_mobile.py -q
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import mobile


# ── the access rule (this is what keeps the LAN-exposed RPC safe) ─────────────

def test_localhost_is_always_allowed_even_without_pairing():
    # the desktop window connects over 127.0.0.1 and must never need a token
    assert mobile.authorized("127.0.0.1", "", enabled=False, real_token="") is True
    assert mobile.authorized("::1", "", enabled=False, real_token="") is True


def test_remote_is_refused_until_mobile_is_enabled():
    assert mobile.authorized("192.168.1.9", "sekret", enabled=False,
                             real_token="sekret") is False


def test_remote_needs_the_correct_token():
    assert mobile.authorized("192.168.1.9", "sekret", enabled=True,
                             real_token="sekret") is True
    assert mobile.authorized("192.168.1.9", "wrong", enabled=True,
                             real_token="sekret") is False
    assert mobile.authorized("192.168.1.9", "", enabled=True,
                             real_token="sekret") is False


def test_remote_refused_when_no_token_is_configured():
    assert mobile.authorized("192.168.1.9", "", enabled=True, real_token="") is False


def test_is_local_variants():
    assert mobile.is_local("127.0.0.1")
    assert mobile.is_local("::1")
    assert not mobile.is_local("192.168.0.5")
    assert not mobile.is_local("10.0.0.2")


# ── token / URL / QR ──────────────────────────────────────────────────────────

def test_token_is_non_trivial_and_unique():
    a, b = mobile.make_token(), mobile.make_token()
    assert len(a) >= 12 and a != b


def test_pairing_url_shape():
    u = mobile.pairing_url("192.168.1.50", 8731, "abc")
    assert u == "http://192.168.1.50:8731/?t=abc"


def test_lan_ip_is_an_ipv4_string():
    ip = mobile.lan_ip()
    parts = ip.split(".")
    assert len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def test_qr_svg_is_self_contained_svg():
    svg = mobile.qr_svg("http://192.168.1.50:8731/?t=SECRETTOKEN")
    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert "<rect" in svg               # the QR modules
    assert "SECRETTOKEN" not in svg     # the payload is encoded, not embedded as text


def test_qr_svg_empty_data_still_returns_svg_or_empty():
    # never raises; qrcode present → svg, absent → ""
    out = mobile.qr_svg("x")
    assert out == "" or out.startswith("<svg")
