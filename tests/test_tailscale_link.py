"""Tests for tailscale_link.py and phone_push.py — no real Tailscale, no real
push service: the CLI and the HTTP post are faked."""
import sys, os, json, base64, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from pathlib import Path
import tailscale_link as ts


STATUS = {"BackendState": "Running",
          "Self": {"DNSName": "omri-pc.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.7"]},
          "CertDomains": ["omri-pc.tail1234.ts.net"]}


class TestTailscale(unittest.TestCase):

    def setUp(self):
        self._orig = (ts._exe, ts._run)
        ts._cache.update(at=0.0, status=None)
        self.calls = []
        self.outputs = {}
        ts._exe = lambda: r"C:\Program Files\Tailscale\tailscale.exe"

        def fake_run(args, timeout=15):
            self.calls.append(args)
            return self.outputs.get(args[0], (0, ""))
        ts._run = fake_run

    def tearDown(self):
        ts._exe, ts._run = self._orig
        ts._cache.update(at=0.0, status=None)

    def test_running_with_https(self):
        st = ts.parse_status(json.dumps(STATUS))
        self.assertEqual(st, {"installed": True, "running": True, "name": "omri-pc.tail1234.ts.net",
                              "https": True, "why": ""})

    def test_signed_out_off_and_no_magicdns(self):
        self.assertEqual(ts.parse_status(json.dumps({**STATUS, "BackendState": "NeedsLogin"}))["why"],
                         "Tailscale isn't signed in on this PC")
        self.assertEqual(ts.parse_status(json.dumps({**STATUS, "BackendState": "Stopped"}))["why"],
                         "Tailscale is turned off on this PC")
        st = ts.parse_status(json.dumps({**STATUS, "Self": {"DNSName": ""}, "CertDomains": None}))
        self.assertIn("MagicDNS", st["why"])
        self.assertFalse(ts.parse_status(json.dumps({**STATUS, "CertDomains": []}))["https"])
        self.assertFalse(ts.parse_status("not json")["running"])

    def test_not_installed(self):
        ts._exe = lambda: ""
        st = ts.status(fresh=True)
        self.assertFalse(st["installed"])
        self.assertEqual(st["why"], "Tailscale isn't installed on this PC")
        self.assertEqual(ts.serve(8741)["ok"], False)

    def test_serve_on_the_tailnet_only(self):
        self.outputs["status"] = (0, json.dumps(STATUS))
        out = ts.serve(8741)
        self.assertEqual(out, {"ok": True, "url": "https://omri-pc.tail1234.ts.net/",
                               "enable_url": "", "why": ""})
        self.assertIn(["serve", "--bg", "8741"], self.calls)
        self.assertFalse(any("funnel" in a for call in self.calls for a in call))   # never public

    def test_serve_asks_for_the_https_switch(self):
        self.outputs["status"] = (0, json.dumps(STATUS))
        self.outputs["serve"] = (124, "Serve is not enabled on your tailnet.\nTo enable, visit:\n\n"
                                      "         https://login.tailscale.com/f/serve?node=nXYZ\n")
        out = ts.serve(8741)
        self.assertFalse(out["ok"])
        self.assertEqual(out["enable_url"], "https://login.tailscale.com/f/serve?node=nXYZ")
        self.assertEqual(out["why"], "HTTPS isn't on for your tailnet yet")

    def test_status_is_cached_for_a_minute(self):
        self.outputs["status"] = (0, json.dumps(STATUS))
        ts.status(); ts.status(); ts.name()
        self.assertEqual(sum(1 for c in self.calls if c[0] == "status"), 1)

    def test_unserve(self):
        ts.unserve()
        self.assertEqual(self.calls, [["serve", "--https=443", "off"]])


class TestPush(unittest.TestCase):
    """The real encryption, a fake push service: the phone can read what's sent."""

    def setUp(self):
        import phone_push
        self.pp = phone_push
        self._orig = (phone_push.KEY_FILE, phone_push.SUBS_FILE)
        tmp = Path(tempfile.mkdtemp())
        phone_push.KEY_FILE, phone_push.SUBS_FILE = tmp / "vapid.pem", tmp / "subs.json"

    def tearDown(self):
        self.pp.KEY_FILE, self.pp.SUBS_FILE = self._orig

    def phone(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        key = ec.generate_private_key(ec.SECP256R1())
        pub = key.public_key().public_bytes(serialization.Encoding.X962,
                                            serialization.PublicFormat.UncompressedPoint)
        auth = os.urandom(16)
        b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")
        return key, auth, {"endpoint": "https://fcm.googleapis.com/fcm/send/abc",
                           "keys": {"p256dh": b64(pub), "auth": b64(auth)}}

    def test_key_pair_is_made_once(self):
        k = self.pp.public_key()
        self.assertEqual(k, self.pp.public_key())
        self.assertEqual(len(base64.urlsafe_b64decode(k + "==")), 65)

    def test_only_real_push_services(self):
        _, _, sub = self.phone()
        self.assertTrue(self.pp.subscribe(sub, "Pixel"))
        for bad in ("https://192.168.1.1/x", "http://fcm.googleapis.com/x",
                    "https://fcm.googleapis.com.evil.example/x"):
            self.assertFalse(self.pp.subscribe({**sub, "endpoint": bad}), bad)
        self.assertFalse(self.pp.subscribe({"endpoint": sub["endpoint"]}))
        self.assertEqual(self.pp.count(), 1)

    def test_sent_encrypted_and_readable_by_the_phone_only(self):
        import http_ece, pywebpush, requests
        key, auth, sub = self.phone()
        self.pp.subscribe(sub, "Pixel")
        sent = {}

        def post(url, data=None, headers=None, timeout=None, **kw):
            sent.update(url=url, data=data, headers=headers)
            r = requests.Response(); r.status_code = 201; r.reason = "Created"; r._content = b""
            return r
        orig = pywebpush.requests.post
        pywebpush.requests.post = post
        try:
            self.assertEqual(self.pp.send({"title": "G.I.L.", "body": "x" * 1000, "tag": "t"},
                                          urgency="high"), 1)
        finally:
            pywebpush.requests.post = orig
        self.assertEqual(sent["url"], sub["endpoint"])
        self.assertEqual(sent["headers"]["Urgency"], "high")
        self.assertTrue(sent["headers"]["Authorization"].startswith("vapid t="))
        self.assertNotIn(b"G.I.L.", sent["data"])                       # unreadable in transit
        plain = json.loads(http_ece.decrypt(sent["data"], private_key=key, auth_secret=auth,
                                            version="aes128gcm"))
        self.assertEqual(plain["title"], "G.I.L.")
        self.assertEqual(len(plain["body"]), self.pp.BODY_CHARS)

    def test_a_phone_that_left_is_forgotten(self):
        import pywebpush, requests
        _, _, sub = self.phone()
        self.pp.subscribe(sub, "Pixel")

        def gone(*a, **k):
            r = requests.Response(); r.status_code = 410; r.reason = "Gone"; r._content = b""
            return r
        orig = pywebpush.requests.post
        pywebpush.requests.post = gone
        try:
            self.assertEqual(self.pp.send({"title": "x", "body": "y"}), 0)
        finally:
            pywebpush.requests.post = orig
        self.assertEqual(self.pp.count(), 0)


if __name__ == "__main__":
    unittest.main()
