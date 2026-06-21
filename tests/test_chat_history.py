"""Tests for chat_history.py — uses a temp SQLite DB, no GUI needed."""
import sys, os, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from pathlib import Path
import chat_history as ch


class TestChatHistory(unittest.TestCase):

    def setUp(self):
        """Redirect to a fresh temp DB for each test."""
        self._orig_path  = ch.DB_PATH
        self._orig_init  = ch._initialized
        self._orig_sess  = ch._current_session
        ch.DB_PATH       = Path(tempfile.mktemp(suffix=".db"))
        ch._initialized  = False
        ch._current_session = ""

    def tearDown(self):
        try:
            ch.DB_PATH.unlink(missing_ok=True)
        except Exception:
            pass
        ch.DB_PATH          = self._orig_path
        ch._initialized     = self._orig_init
        ch._current_session = self._orig_sess

    # ── Basic save & load ─────────────────────────────────────────────────────

    def test_save_and_load(self):
        ch.init_session()
        ch.save_message("user", "Hello GIL")
        ch.save_message("gil",  "Hello! How can I help?")
        msgs = ch.load_recent(10)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["sender"],  "user")
        self.assertEqual(msgs[0]["content"], "Hello GIL")
        self.assertEqual(msgs[1]["sender"],  "gil")

    def test_ordering_oldest_first(self):
        ch.init_session()
        ch.save_message("user", "first")
        time.sleep(0.01)
        ch.save_message("user", "second")
        msgs = ch.load_recent(10)
        self.assertEqual(msgs[0]["content"], "first")
        self.assertEqual(msgs[1]["content"], "second")

    # ── Edge cases ────────────────────────────────────────────────────────────

    def test_empty_message_skipped(self):
        ch.init_session()
        ch.save_message("user", "")
        ch.save_message("user", "   ")
        self.assertEqual(len(ch.load_recent(10)), 0)

    def test_limit_respected(self):
        ch.init_session()
        for i in range(20):
            ch.save_message("user", f"message {i}")
        msgs = ch.load_recent(5)
        self.assertEqual(len(msgs), 5)

    def test_returns_newest_when_limited(self):
        ch.init_session()
        for i in range(10):
            ch.save_message("user", f"msg {i}")
        msgs = ch.load_recent(3)
        # Should be the LAST 3 (most recent)
        self.assertEqual(msgs[-1]["content"], "msg 9")

    # ── Session markers ───────────────────────────────────────────────────────

    def test_first_message_of_session_flagged(self):
        ch.init_session()
        ch.save_message("user", "first in session")
        ch.save_message("user", "second in session")
        msgs = ch.load_recent(10)
        self.assertTrue(msgs[0]["is_session_start"])
        self.assertFalse(msgs[1]["is_session_start"])

    def test_no_session_no_save(self):
        # save without init_session should silently no-op
        ch.save_message("user", "orphan message")
        self.assertEqual(len(ch.load_recent(10)), 0)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    def test_clear_old(self):
        ch.init_session()
        # Manually insert a very old message
        conn = ch._get_conn()
        conn.execute(
            "INSERT INTO messages (session_id, sender, content, ts) VALUES (?,?,?,?)",
            ("old-session", "user", "ancient msg", time.time() - 40 * 86400),
        )
        conn.commit(); conn.close()
        ch.save_message("user", "recent msg")
        ch.clear_old(days=30)
        msgs = ch.load_recent(100)
        contents = [m["content"] for m in msgs]
        self.assertNotIn("ancient msg", contents)
        self.assertIn("recent msg", contents)


if __name__ == "__main__":
    unittest.main()
