"""
Tests for action_router.dispatch() — the brain response dispatcher.
Mocks the engine object so no GUI, voice, or network is needed.
"""
import sys, os, threading
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from unittest.mock import MagicMock, patch, call


def _make_engine(action_results=None):
    """Build a minimal mock ConversationEngine for dispatch() tests."""
    eng = MagicMock()
    eng._speak = MagicMock()
    eng.window = MagicMock()
    eng._last_spoke_at = [0.0]
    eng._last_said     = [""]
    eng._active_project = [None]
    eng._camera_win    = [None]
    eng._execute_action = MagicMock(return_value=action_results)
    eng._dispatch_instant = MagicMock()
    eng._start_gesture_watcher = MagicMock()
    eng._stop_gesture_watcher  = MagicMock()
    return eng


class TestDispatchSync(unittest.TestCase):
    """Actions that return False (synchronous, no early return)."""

    def _dispatch(self, action, target="", speech="", text="", lower="",
                  extra=None, engine=None):
        from action_router import dispatch
        eng = engine or _make_engine()
        result = dispatch(action, target, speech, text, lower or action,
                          extra or [], eng)
        return result, eng

    def test_show_settings_opens_window(self):
        result, eng = self._dispatch("show_settings")
        self.assertFalse(result)
        eng.window.after.assert_called()

    def test_list_tasks_refreshes(self):
        result, eng = self._dispatch("list_tasks")
        self.assertFalse(result)
        eng.window.refresh_tasks.assert_called_once()

    def test_system_vitals_calls_execute(self):
        result, eng = self._dispatch("system_vitals")
        self.assertFalse(result)
        eng._execute_action.assert_called_with("system_vitals", "")

    def test_set_mode_calls_execute(self):
        result, eng = self._dispatch("set_mode", target="dnd")
        self.assertFalse(result)
        eng._execute_action.assert_called_with("set_mode", "dnd")

    def test_note_calls_execute(self):
        result, eng = self._dispatch("note", target="buy milk")
        self.assertFalse(result)
        eng._execute_action.assert_called_with("note", "buy milk")

    def test_unknown_action_returns_false(self):
        result, eng = self._dispatch("totally_unknown_action")
        self.assertFalse(result)

    def test_close_camera_clears_state(self):
        eng = _make_engine()
        cam = MagicMock()
        eng._camera_win[0] = cam
        from action_router import dispatch
        dispatch("close_camera", "", "", "", "close_camera", [], eng)
        cam.close.assert_called_once()
        self.assertIsNone(eng._camera_win[0])
        eng._stop_gesture_watcher.assert_called_once()


class TestDispatchAsync(unittest.TestCase):
    """Actions that return True (async thread took over)."""

    def _dispatch(self, action, target="", speech="", text="", lower="",
                  extra=None, engine=None):
        from action_router import dispatch
        eng = engine or _make_engine()
        result = dispatch(action, target, speech, text, lower or action,
                          extra or [], eng)
        return result, eng

    def test_weather_returns_true(self):
        result, _ = self._dispatch("weather")
        self.assertTrue(result)

    def test_list_reminders_returns_true(self):
        result, _ = self._dispatch("list_reminders")
        self.assertTrue(result)

    def test_briefing_returns_true(self):
        result, _ = self._dispatch("briefing")
        self.assertTrue(result)

    def test_calendar_returns_true(self):
        result, _ = self._dispatch("calendar")
        self.assertTrue(result)

    def test_news_returns_true(self):
        result, _ = self._dispatch("news")
        self.assertTrue(result)

    def test_build_website_returns_true(self):
        result, _ = self._dispatch("build_website", target="coffee shop site")
        self.assertTrue(result)

    def test_look_returns_true(self):
        result, _ = self._dispatch("look")
        self.assertTrue(result)


class TestExtraActions(unittest.TestCase):
    """Extra multi-task actions are dispatched via _dispatch_instant."""

    def test_extra_action_dispatched(self):
        from action_router import dispatch
        eng = _make_engine()
        extras = [{"action": "open_url", "target": "https://github.com"}]
        dispatch("note", "test", "", "", "note", extras, eng)
        eng._dispatch_instant.assert_called_once_with(
            "open_url", "https://github.com"
        )

    def test_extra_build_rerouted_to_webgen_on_web_word(self):
        from action_router import dispatch
        eng = _make_engine()
        extras = [{"action": "build", "target": "portfolio"}]
        # "website" is in lower → reroute build→build_website
        dispatch("note", "test", "", "", "make a website", extras, eng)
        eng._dispatch_instant.assert_called_once()
        args = eng._dispatch_instant.call_args[0]
        self.assertEqual(args[0], "build_website")


if __name__ == "__main__":
    unittest.main()
