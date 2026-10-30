"""Is every Groq model G.I.L. names still served?

The rest of the suite mocks the network, which is how it stayed green for
months while Groq retired the models behind vision, memory learning, the
website builder, 3D Studio and half the router. This one asks Groq.
Skipped offline or without a key; run it before every release.
"""
import sys, os
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

import model_health as mh

LIVE = mh.live(force=True)
pytestmark = pytest.mark.skipif(LIVE is None, reason="Groq unreachable or no key")


def test_every_groq_model_written_in_the_source_is_served():
    gone = {m: where for m, where in mh.in_source().items() if m not in LIVE}
    assert not gone, "Groq no longer serves: " + "; ".join(
        f"{m} ({', '.join(where[:3])})" for m, where in sorted(gone.items()))


def test_every_configured_model_is_served():
    assert mh.dead() == {}


def test_the_scan_actually_finds_models():
    # guards the guard: a regex that matched nothing would pass the test above
    found = mh.in_source()
    assert "openai/gpt-oss-120b" in found and "qwen/qwen3.8-27b" in found
