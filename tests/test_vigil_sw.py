"""The phone app's service worker (web_chat/vigil_sw.js), run under Node in a
fake browser: what a push shows, and what tapping its buttons does. Skipped
when Node isn't installed."""
import json, os, shutil, subprocess, unittest

HERE = os.path.dirname(__file__)


@unittest.skipUnless(shutil.which("node"), "Node isn't installed")
class TestServiceWorker(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        done = subprocess.run(["node", os.path.join(HERE, "sw_harness.mjs")],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)
        if done.returncode:
            raise AssertionError(done.stderr)
        cls.out = json.loads(done.stdout.strip().splitlines()[-1])

    def test_a_push_becomes_a_notification_with_two_buttons(self):
        n = self.out["shown"]
        self.assertEqual(n["title"], "G.I.L. · Uni")
        self.assertEqual(n["body"], "Can I? Remind you")
        self.assertEqual(n["tag"], "msg-7")
        self.assertEqual(n["badge"], "/gil_badge_96.png")
        self.assertEqual([a["action"] for a in n["actions"]], ["approve", "deny"])
        self.assertTrue(n["renotify"])

    def test_do_it_presses_the_button_on_the_pc_with_the_key(self):
        f = self.out["fetch"]
        self.assertEqual(f["url"], "/api")
        self.assertEqual(f["headers"]["X-Vigil"], "1")
        self.assertEqual(f["headers"]["X-GIL-Token"], "secret-key")
        self.assertEqual(f["body"], {"method": "button", "args": [7, "approve"]})
        self.assertTrue(self.out["closed"])
        self.assertEqual(self.out["openedAfterAction"], 0)              # no need to open the app
        self.assertEqual(self.out["confirm"]["body"], "Done ✓ I'll remind you")
        self.assertTrue(self.out["confirm"]["silent"])

    def test_pc_out_of_reach_opens_the_app(self):
        self.assertEqual(self.out["openedWhenUnreachable"], ["/?app=vigil#m9"])

    def test_a_tap_focuses_the_open_app_at_that_message(self):
        self.assertEqual(self.out["focused"], 1)
        self.assertEqual(self.out["posted"], {"jump": 11})

    def test_working_on_it_is_silent(self):
        n = self.out["silent"]
        self.assertTrue(n["silent"])
        self.assertFalse(n["renotify"])
        self.assertIsNone(n.get("vibrate"))


if __name__ == "__main__":
    unittest.main()
