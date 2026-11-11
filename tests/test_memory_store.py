"""Tests for GIL's two memory modules: memory.py (recall) and profile_store.py
(profile). Every test runs against a throwaway database and history file, so
nothing touches the real data/ folder's contents."""

import io
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import memory  # noqa: E402
import profile_store  # noqa: E402


class _TempStore(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._saved = (memory.DB_PATH, memory._HISTORY_PATH, profile_store.DB_PATH)
        memory.DB_PATH = tmp / "gil_brain.db"
        memory._HISTORY_PATH = tmp / "session_memory.json"
        profile_store.DB_PATH = tmp / "gil_brain.db"
        memory._init_db()
        profile_store._init_db()
        profile_store._current_goal = None
        memory._session_goals.clear()
        self._out = redirect_stdout(io.StringIO())
        self._out.__enter__()

    def tearDown(self):
        self._out.__exit__(None, None, None)
        memory.DB_PATH, memory._HISTORY_PATH, profile_store.DB_PATH = self._saved
        profile_store._current_goal = None
        memory._session_goals.clear()
        self._tmp.cleanup()


class TestSharedDatabase(_TempStore):
    def test_both_modules_keep_their_tables_in_one_file(self):
        conn = sqlite3.connect(str(memory.DB_PATH))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        conn.close()
        for name in ("memories", "memory_fts", "session", "schedule_patterns",
                     "preferences", "goals"):
            self.assertIn(name, tables)


class TestRecall(_TempStore):
    def test_remember_deduplicates_and_recall_finds_it(self):
        a = memory.remember("Omri prefers dark themes everywhere", "preference")
        b = memory.remember("Omri prefers dark themes everywhere", "preference")
        self.assertEqual(a, b)
        found = memory.recall("dark themes")
        self.assertEqual([m["content"] for m in found],
                         ["Omri prefers dark themes everywhere"])

    def test_memory_context_has_last_session(self):
        memory.record_task("fix the CI", "done")
        ctx = memory.build_memory_context("hello")
        self.assertIn('LAST SESSION (just now): "fix the CI"', ctx)

    def test_history_keeps_only_conversation_and_trims(self):
        history = [{"role": "system", "content": "prompt"}]
        for i in range(12):
            history += [{"role": "user", "content": f"q{i}"},
                        {"role": "assistant", "content": f"a{i}"}]
        memory.save_history(history)
        loaded = memory.load_history()
        self.assertEqual(len(loaded), memory.MAX_PAIRS * 2)
        self.assertEqual(loaded[0]["content"], "q4")
        self.assertTrue(all(m["role"] != "system" for m in loaded))

    def test_old_history_is_ignored(self):
        memory._HISTORY_PATH.write_text(json.dumps(
            {"saved_at": time.time() - memory.MAX_AGE - 5,
             "history": [{"role": "user", "content": "stale"}]}), encoding="utf-8")
        self.assertEqual(memory.load_history(), [])

    def test_missing_history_is_empty(self):
        self.assertEqual(memory.load_history(), [])


class TestSessionLifecycle(_TempStore):
    def test_greeting_mentions_recent_task(self):
        memory.record_task("fix the CI")
        self.assertIn("Last session you covered: fix the CI",
                      memory.build_startup_greeting("Omri"))

    def test_greeting_falls_back_to_active_goal(self):
        profile_store.set_goal("refactor memory")
        self.assertIn("Picking up from refactor memory",
                      memory.build_startup_greeting("Omri"))

    def test_shutdown_saves_session_goal_as_memory(self):
        memory.record_session_goal("ship the PR")
        memory.trigger_shutdown("Omri")
        found = memory.recall("ship the PR")
        self.assertTrue(any("worked on 'ship the PR'" in m["content"] for m in found))

    def test_session_context_lists_goals(self):
        memory.record_session_goal("ship the PR")
        self.assertIn("Goals this session: ship the PR", memory.build_session_context())


class TestPreferences(_TempStore):
    def test_set_get_delete(self):
        profile_store.set_preference("ui", "theme", "dark", confidence=0.9)
        self.assertEqual(profile_store.get_preference("ui", "theme"), "dark")
        profile_store.delete_preference("ui", "theme")
        self.assertEqual(profile_store.get_preference("ui", "theme", "none"), "none")

    def test_learns_explicit_preference(self):
        profile_store.learn_from_exchange("I prefer python type hints in every function")
        prefs = profile_store.list_preferences("coding")
        self.assertEqual(len(prefs), 1)
        self.assertEqual(prefs[0]["value"], "python type hints in every function")

    def test_context_only_has_confident_preferences(self):
        profile_store.set_preference("ui", "theme", "dark", confidence=0.9)
        profile_store.set_preference("ui", "font", "mono", confidence=0.3)
        ctx = profile_store.build_preference_context()
        self.assertIn("theme: dark", ctx)
        self.assertNotIn("mono", ctx)


class TestGoals(_TempStore):
    def test_goal_from_reply_and_context(self):
        profile_store.update_from_reply("I'm working on the memory refactor", app="code")
        self.assertEqual(profile_store.get_goal_text(), "the memory refactor")
        ctx = profile_store.build_goal_context()
        self.assertIn("ACTIVE GOAL: the memory refactor", ctx)
        self.assertIn("App: code", ctx)

    def test_new_goal_replaces_old_and_clear_ends_it(self):
        profile_store.set_goal("first")
        profile_store.set_goal("second")
        self.assertEqual(profile_store._load_active_goal()["description"], "second")
        profile_store.clear_goal()
        self.assertIsNone(profile_store._load_active_goal())
        self.assertEqual(profile_store.build_goal_context(), "")


class TestSeed(_TempStore):
    def test_seed_is_safe_to_rerun(self):
        profile_store.seed()
        profile_store.seed()
        conn = sqlite3.connect(str(memory.DB_PATH))
        n_mem = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
        n_pref = conn.execute("SELECT COUNT(*) FROM preferences").fetchone()[0]
        conn.close()
        self.assertEqual(n_mem, len(profile_store.FACTS))
        self.assertEqual(n_pref, len(profile_store.PREFS))


if __name__ == "__main__":
    unittest.main()
