"""
Tests for GILBrain JSON parsing and response normalisation.
Mocks the Groq API so no network or API key is needed.
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import patch, MagicMock


def _make_mock_response(content: str, status: int = 200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    resp.raise_for_status = MagicMock()
    return resp


def _query_with_mock(raw_json: str):
    """Run GILBrain.query() with a mocked Groq response."""
    from gil_brain import GILBrain, _session
    brain = GILBrain.__new__(GILBrain)
    brain.username = "TestUser"
    brain.history  = []

    mock_resp = _make_mock_response(raw_json)

    with patch("gil_brain._session") as mock_sess:
        mock_sess.post.return_value = mock_resp
        with patch("gil_brain.GROQ_KEYS", ["fake-key"]):
            # Patch heavy context builders to avoid real imports
            with patch("gil_brain._load_user_profile", return_value="Name: Test"):
                with patch.multiple("gil_brain",
                                    build_memory_context=MagicMock(return_value=""),
                                    get_screen_context=MagicMock(return_value=""),
                                    get_desktop_projects=MagicMock(return_value=[])):
                    try:
                        result = brain.query("test input")
                    except Exception:
                        result = brain.query.__wrapped__(brain, "test input") \
                            if hasattr(brain.query, "__wrapped__") else None
    return result, brain


class TestBrainParsing(unittest.TestCase):
    """_parse_json handles various LLM output formats."""

    def test_clean_json(self):
        from gil_brain import _parse_json
        raw = '{"speech": "Done.", "actions": [], "report": null}'
        parsed = _parse_json(raw)
        self.assertEqual(parsed["speech"], "Done.")
        self.assertEqual(parsed["extra_actions"], [])

    def test_json_with_markdown_fences(self):
        from gil_brain import _parse_json
        raw = '```json\n{"speech": "OK", "actions": [], "report": null}\n```'
        parsed = _parse_json(raw)
        self.assertEqual(parsed["speech"], "OK")

    def test_actions_array_normalised(self):
        from gil_brain import _parse_json
        raw = ('{"speech": "Opening.", '
               '"actions": [{"action": "open_url", "target": "https://github.com"}], '
               '"report": null}')
        parsed = _parse_json(raw)
        self.assertEqual(parsed["action"],  "open_url")
        self.assertEqual(parsed["target"],  "https://github.com")
        self.assertEqual(parsed["extra_actions"], [])

    def test_multi_action_response(self):
        from gil_brain import _parse_json
        raw = ('{"speech": "Done.", '
               '"actions": ['
               '  {"action": "open_url", "target": "https://youtube.com"},'
               '  {"action": "pc_volume", "target": "set 40"}'
               '], "report": null}')
        parsed = _parse_json(raw)
        self.assertEqual(parsed["action"],  "open_url")
        self.assertEqual(len(parsed["extra_actions"]), 1)
        self.assertEqual(parsed["extra_actions"][0]["action"], "pc_volume")

    def test_partial_parse_extracts_speech(self):
        from gil_brain import _parse_json
        raw = 'Some preamble {"speech": "Hello.", broken json'
        parsed = _parse_json(raw)
        # Should extract speech even from broken JSON
        self.assertEqual(parsed.get("speech"), "Hello.")

    def test_empty_raw_returns_empty_speech(self):
        from gil_brain import _parse_json
        parsed = _parse_json("")
        self.assertEqual(parsed.get("speech", ""), "")

    def test_no_actions_key_defaults(self):
        from gil_brain import _parse_json
        # Old single-action format
        raw = '{"speech": "Opening.", "action": "open_app", "target": "chrome", "report": null}'
        parsed = _parse_json(raw)
        self.assertEqual(parsed["action"], "open_app")
        self.assertEqual(parsed["target"], "chrome")


class TestHistoryManagement(unittest.TestCase):
    """History append/pop behaviour on success and error."""

    def test_history_pop_on_failure(self):
        """A failed query must not leave an orphaned user message in history."""
        from gil_brain import GILBrain
        brain = GILBrain.__new__(GILBrain)
        brain.username = "Test"
        brain.history  = []

        # Simulate a connection error
        import requests as _req
        with patch("gil_brain._session") as ms, \
             patch("gil_brain.GROQ_KEYS", ["k"]):
            ms.post.side_effect = _req.exceptions.ConnectionError("no network")
            try:
                brain.query("hello")
            except Exception:
                pass

        # History must be empty — user message should have been popped
        self.assertEqual(len(brain.history), 0,
                         "Orphaned user message left in history after ConnectionError")


if __name__ == "__main__":
    unittest.main()
