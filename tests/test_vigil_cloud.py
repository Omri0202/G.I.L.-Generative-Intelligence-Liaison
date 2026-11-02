"""Tests for Vigil in the cloud: the encrypted store (a local git repository
stands in for GitHub), the chat server in cloud mode, and the PC's link (a
fake server stands in for the cloud). Nothing reaches GitHub, Render, the
user's real chat or their phone."""
import sys, os, io, json, queue, sqlite3, subprocess, tarfile, tempfile, threading, time
import urllib.request, urllib.error
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from cryptography.fernet import Fernet

import cloud_store
import vigil
import vigil_chat as vc
import vigil_link
from tests.test_vigil_chat import ChatCase

SECRET = Fernet.generate_key().decode()


def _fill(data: Path) -> None:
    data.mkdir(parents=True, exist_ok=True)
    (data / "vigil.json").write_text('{"orders": []}', encoding="utf-8")
    db = sqlite3.connect(data / "vigil_chat.db")
    db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, text TEXT)")
    db.execute("INSERT INTO messages(text) VALUES ('שלום')")
    db.commit()
    db.close()
    (data / "gil.log").write_text("not kept", encoding="utf-8")
    voice = data / "vigil_voice"
    voice.mkdir()
    for i in range(3):
        f = voice / f"gil-{i:012d}.mp3"
        f.write_bytes(b"ID3" + bytes([i]))
        os.utime(f, (1000 + i, 1000 + i))


class TestBundle(unittest.TestCase):

    def test_round_trip_keeps_only_what_vigil_needs(self):
        src, dst = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp())
        _fill(src)
        orig = cloud_store.VOICE_KEEP
        cloud_store.VOICE_KEEP = 2
        try:
            blob = cloud_store.seal(cloud_store.pack(src), SECRET)
        finally:
            cloud_store.VOICE_KEEP = orig
        self.assertNotIn(b"orders", blob)                          # ciphertext, not the state
        names = cloud_store.unpack(cloud_store.unseal(blob, SECRET), dst)
        self.assertIn("vigil.json", names)
        self.assertFalse((dst / "gil.log").exists())
        rows = sqlite3.connect(dst / "vigil_chat.db").execute("SELECT text FROM messages").fetchall()
        self.assertEqual(rows, [("שלום",)])
        kept = sorted(f.name for f in (dst / "vigil_voice").iterdir())
        self.assertEqual(kept, ["gil-000000000001.mp3", "gil-000000000002.mp3"])   # the newest

    def test_the_wrong_key_opens_nothing(self):
        blob = cloud_store.seal(b"x", SECRET)
        with self.assertRaises(cloud_store.StoreError):
            cloud_store.unseal(blob, Fernet.generate_key().decode())

    def test_a_bundle_cant_write_outside_data(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for name in ("../evil.py", "vigil_voice/../../evil.py", "main.py", "vigil.json"):
                info = tarfile.TarInfo(name)
                info.size = 2
                tar.addfile(info, io.BytesIO(b"{}"))
        root = Path(tempfile.mkdtemp())
        data = root / "data"
        names = cloud_store.unpack(buf.getvalue(), data)
        self.assertEqual(names, ["vigil.json"])
        self.assertFalse((root / "evil.py").exists())
        self.assertFalse((data / "main.py").exists())

    def test_base64_deploy_key(self):
        import base64
        key = "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----\n"
        self.assertEqual(cloud_store._key_text(base64.b64encode(key.encode()).decode()), key)
        self.assertEqual(cloud_store._key_text(key), key.strip())


class TestStoreRepo(unittest.TestCase):
    """A bare repository on disk in GitHub's place."""

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp()) / "data.git"
        subprocess.run(["git", "init", "-q", "--bare", str(self.repo)], check=True)
        self.data = Path(tempfile.mkdtemp())
        _fill(self.data)

    def store(self, data):
        return cloud_store.Store(data, str(self.repo), SECRET)

    def test_nothing_is_saved_before_the_repo_has_been_read(self):
        st = self.store(self.data)
        self.assertFalse(st.save())                               # could overwrite real memory

    def test_empty_then_saved_then_restored_elsewhere(self):
        st = self.store(self.data)
        self.assertEqual(st.restore(), "empty")
        self.assertTrue(st.save())
        self.assertFalse(st.save())                               # unchanged: no new write
        (self.data / "vigil.json").write_text('{"orders": [1]}', encoding="utf-8")
        self.assertTrue(st.save())
        other = Path(tempfile.mkdtemp())
        self.assertEqual(self.store(other).restore(), "restored")
        self.assertEqual((other / "vigil.json").read_text(encoding="utf-8"), '{"orders": [1]}')
        log = subprocess.run(["git", "--git-dir", str(self.repo), "log", "--oneline", "main"],
                             capture_output=True, text=True, check=True).stdout
        self.assertEqual(len(log.splitlines()), 1)                 # no history, only the latest

    def test_an_unreadable_store_stays_untouched(self):
        st = cloud_store.Store(self.data, str(self.repo.parent / "missing.git"), SECRET)
        with self.assertRaises(cloud_store.StoreError):
            st.restore()
        self.assertFalse(st.save())

    def test_wrong_key_stays_untouched(self):
        st = self.store(self.data)
        st.restore()
        st.save()
        wrong = cloud_store.Store(Path(tempfile.mkdtemp()), str(self.repo),
                                  Fernet.generate_key().decode())
        with self.assertRaises(cloud_store.StoreError):
            wrong.restore()
        self.assertFalse(wrong.save())


KEY = "pc-key-" + "x" * 30
HOST = "gil-vigil.onrender.com"


class CloudCase(ChatCase):
    """The chat server as it runs in the cloud, with a fake PC link."""

    def setUp(self):
        super().setUp()
        self._cloud = (vc.CLOUD, vc.CLOUD_KEY, vc.CLOUD_HOST, dict(vc._agent), dict(vc._gil),
                       vc.idle_seconds)
        vc.CLOUD, vc.CLOUD_KEY, vc.CLOUD_HOST = True, KEY, HOST
        vc.idle_seconds = self._orig["idle_seconds"]               # the cloud's own: from the PC
        vc._agent.update(q=None, idle=float(vc.AWAY_SECS), at=time.time(), gil=False)
        vc.connect_gil(send=vc._pc_send, busy=vc._pc_busy, show=vc._pc_show)
        vc._job.update(**{"from": "", "text": "", "since": 0.0, "last": None, "result": "",
                          "noticed": False})
        self.events = []

    def tearDown(self):
        q = vc._agent["q"]
        if q is not None:
            vc._agent_detach(q)
            q.put_nowait(None)                                     # the fake link stops too
        (vc.CLOUD, vc.CLOUD_KEY, vc.CLOUD_HOST, agent, gil, vc.idle_seconds) = self._cloud
        vc._agent.update(agent)
        vc._gil.update(gil)
        super().tearDown()

    def pc_on(self, gil=True, idle=0.0, answer=None):
        """A PC whose link answers questions with answer(ev) and keeps the rest."""
        q = queue.Queue()
        vc._agent_attach(q)
        vc._agent_presence(idle=idle, gil=gil)

        def run():
            while True:
                raw = q.get()
                if raw is None:
                    return
                ev = json.loads(raw)
                self.events.append(ev)
                if ev.get("id"):
                    vc._agent_result(id=ev["id"], result=(answer or (lambda e: None))(ev))
        threading.Thread(target=run, daemon=True, name="GIL-FakeLink").start()
        return q


class TestTheCloudKnowsThePC(CloudCase):

    def test_pc_off_is_away_and_gil_isnt_running(self):
        self.assertFalse(vc.pc_online())
        self.assertFalse(vc.gil_running())
        self.assertGreaterEqual(vc.idle_seconds(), vc.AWAY_SECS)

    def test_presence_from_the_pc(self):
        self.pc_on(gil=True, idle=12)
        self.assertTrue(vc.pc_online())
        self.assertTrue(vc.gil_running())
        self.assertLess(vc.idle_seconds(), 15)
        vc._agent["at"] -= vc.AGENT_STALE + 1                     # it went quiet: asleep
        self.assertFalse(vc.pc_online())
        self.assertFalse(vc.gil_running())

    def test_a_job_runs_on_the_pc_and_its_reply_comes_back(self):
        def answer(ev):
            if ev["kind"] == "busy":
                return False
            if ev["kind"] == "send":
                vc._agent_gil_event("message", ["Opening Spotify for you 🎵", "gil"])
                return True
        self.pc_on(answer=answer)
        vc._hand_to_gil("open spotify on my pc")
        kinds = [e["kind"] for e in self.events]
        self.assertIn("send", kinds)
        self.assertEqual([e["text"] for e in self.events if e["kind"] == "show"],
                         ["📱 open spotify on my pc"])
        self.assertEqual(self.last()["text"], "Opening Spotify for you 🎵")
        self.assertEqual(vc._job["from"], "")
        deadline = time.time() + 2
        while "toast" not in [e["kind"] for e in self.events] and time.time() < deadline:
            time.sleep(0.02)
        self.assertIn("toast", [e["kind"] for e in self.events])  # the PC's desktop, not this server's
        self.assertEqual(self.toasts, [])

    def test_the_pc_going_off_mid_job_is_no_done(self):
        q = None

        def answer(ev):
            if ev["kind"] == "send":
                vc._agent_bye()                                    # shut down mid-job
            return False if ev["kind"] == "busy" else None
        q = self.pc_on(answer=answer)
        vc._hand_to_gil("open spotify")
        texts = [m["text"] for m in vc.messages() if m["sender"] == "gil"]
        self.assertIn("went off before that was done", texts[-1])
        self.assertNotIn("Done ✓", " ".join(texts))
        self.assertEqual(vc._job["from"], "")
        self.assertFalse(vc.pc_online())

    def test_with_the_pc_off_a_job_waits(self):
        vc._hand_to_gil("open spotify")
        self.assertIn("PC is off", self.last()["text"])

    def test_the_pc_going_off_mid_question_doesnt_hang(self):
        q = queue.Queue()
        vc._agent_attach(q)
        vc._agent_presence(gil=True)
        threading.Timer(0.2, vc._agent_detach, args=(q,)).start()
        began = time.time()
        self.assertIsNone(vc._agent_call("busy", 30))
        self.assertLess(time.time() - began, 5)

    def test_checkout_with_the_pc_off_gives_the_link(self):
        offer = {"seller": "Andalu", "name": "Poland 10GB", "price": "₪39",
                 "url": "https://andalu.com/poland"}
        vc._checkout_job(offer, None, False)
        text = self.last()["text"]
        self.assertIn("PC is off", text)
        self.assertIn("https://andalu.com/poland", text)

    def test_checkout_runs_on_the_pc(self):
        res = {"ok": True, "state": "payment", "why": "", "url": "https://andalu.com/pay",
               "trace": []}
        self.pc_on(answer=lambda ev: res if ev["kind"] == "checkout" else None)
        offer = {"seller": "Andalu", "name": "Poland 10GB", "price": "₪39", "url": "https://andalu.com"}
        vc._checkout_job(offer, None, False)
        self.assertIn("payment page", self.last()["text"])
        ev = [e for e in self.events if e["kind"] == "checkout"][0]
        self.assertEqual(ev["offer"]["seller"], "Andalu")

    def test_opening_a_page_happens_on_the_pc(self):
        import vigil_actions as va
        ok, said = va.execute({"type": "open_page", "url": "https://example.com"})
        self.assertFalse(ok)
        self.assertIn("https://example.com", said)
        self.pc_on()
        ok, _ = va.execute({"type": "open_page", "url": "https://example.com"})
        self.assertTrue(ok)
        deadline = time.time() + 2
        opened = []
        while not opened and time.time() < deadline:
            opened = [e for e in self.events if e["kind"] == "open_url"]
            time.sleep(0.02)
        self.assertEqual(opened, [{"kind": "open_url", "url": "https://example.com"}])

    def test_the_phone_code_is_the_servers_address(self):
        v = vc.api_phone(True)
        self.assertEqual(v["via"], "cloud")
        self.assertTrue(v["url"].startswith(f"https://{HOST}/?app=vigil&t="))
        self.assertEqual(vc._phone()["via"], "cloud")
        self.assertTrue(vc._phone_state()["cloud"])


class TestCloudServer(CloudCase):
    """Who gets in, over real HTTP."""

    def setUp(self):
        super().setUp()
        self.srv = vc._Server(("127.0.0.1", 0), vc._Handler)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def req(self, path, method="GET", body=None, key="", host=HOST):
        headers = {"Host": host, "X-Vigil": "1", "Content-Type": "application/json"}
        if key:
            headers["X-GIL-Token"] = key
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(r, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, b""

    def test_health_is_open(self):
        self.assertEqual(self.req("/health", host="anything.example"), (200, b"ok"))

    def test_the_pcs_key_opens_everything(self):
        status, body = self.req("/api", "POST", {"method": "state", "args": []}, key=KEY)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["result"]["phone"]["cloud"])
        self.assertEqual(self.req("/api", "POST", {"method": "phone_check", "args": []},
                                  key=KEY)[0], 200)

    def test_no_key_or_a_wrong_host_gets_nothing(self):
        self.assertEqual(self.req("/api", "POST", {"method": "state", "args": []})[0], 403)
        self.assertEqual(self.req("/api", "POST", {"method": "state", "args": []}, key="nope")[0], 403)
        self.assertEqual(self.req("/api", "POST", {"method": "state", "args": []}, key=KEY,
                                  host="evil.example")[0], 403)
        self.assertEqual(self.req("/", host=HOST)[0], 200)        # the page itself is no secret

    def test_a_phone_chats_but_isnt_the_pc(self):
        token = vc.api_phone(True)["url"].split("t=", 1)[1]
        self.assertEqual(self.req("/api", "POST", {"method": "state", "args": []}, key=token)[0], 200)
        self.assertEqual(self.req("/api", "POST", {"method": "phone", "args": [False]},
                                  key=token)[0], 403)
        self.assertEqual(self.req("/agent", "POST", {"method": "presence", "kwargs": {}},
                                  key=token)[0], 403)
        self.assertEqual(self.req("/agent/events", key=token)[0], 403)

    def test_the_pcs_link_reports_in(self):
        status, body = self.req("/agent", "POST",
                                {"method": "presence", "kwargs": {"idle": 3, "gil": True}}, key=KEY)
        self.assertEqual(status, 200)
        self.assertIn("unread", json.loads(body)["result"])
        self.assertEqual(vc._agent["idle"], 3.0)

    def test_the_link_stream(self):
        r = urllib.request.Request(self.base + "/agent/events",
                                   headers={"Host": HOST, "X-GIL-Token": KEY})
        with urllib.request.urlopen(r, timeout=5) as resp:
            first = resp.readline()
            self.assertTrue(first.startswith(b"data: "))
            self.assertEqual(json.loads(first[6:])["kind"], "unread")
            vc._agent_presence(gil=True)
            self.assertTrue(vc._agent_send("toast", body="hi", urgency="normal"))
            resp.readline()
            ev = json.loads(resp.readline()[6:])
            self.assertEqual(ev, {"kind": "toast", "body": "hi", "urgency": "normal"})


# ── the PC's side ─────────────────────────────────────────────────────────────

class _FakeCloud(BaseHTTPRequestHandler):
    events: queue.Queue
    posts: list

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.headers.get("X-GIL-Token") != KEY:
            self.send_response(403)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        while True:
            try:
                ev = self.events.get(timeout=0.5)
            except queue.Empty:
                try:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                except OSError:
                    return
                continue
            if ev is None:
                return
            self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
            self.wfile.flush()

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.posts.append(req)
        result = {"unread": 4} if req["method"] == "presence" else True
        body = json.dumps({"result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class TestTheLink(unittest.TestCase):

    def setUp(self):
        _FakeCloud.events, _FakeCloud.posts = queue.Queue(), []
        while not vigil_link._outbox.empty():
            vigil_link._outbox.get_nowait()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _FakeCloud)
        self.srv.daemon_threads = True
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = (vigil_link.CONFIG, vc._register_protocol, dict(vc._gil), vc._role,
                      vc.idle_seconds)
        vigil_link.CONFIG = self.tmp / "vigil_cloud.json"
        vigil_link.CONFIG.write_text(json.dumps(
            {"url": f"http://127.0.0.1:{self.srv.server_address[1]}", "key": KEY}), encoding="utf-8")
        vc._register_protocol = lambda: None
        vc.idle_seconds = lambda: 7.0
        self.sent = []
        vc._gil.update(send=lambda t: self.sent.append(t), busy=lambda: False, show=None)
        import notify
        self._toast = notify.toast
        self.toasts = []
        notify.toast = lambda *a, **k: self.toasts.append(a) or True

    def tearDown(self):
        vigil_link.stop()
        _FakeCloud.events.put(None)
        self.srv.shutdown()
        self.srv.server_close()
        import notify
        notify.toast = self._toast
        (vigil_link.CONFIG, vc._register_protocol, gil, vc._role, vc.idle_seconds) = self._orig
        vc._gil.clear()
        vc._gil.update(gil)

    def wait_for(self, check, secs=5):
        deadline = time.time() + secs
        while time.time() < deadline:
            if check():
                return True
            time.sleep(0.05)
        return False

    def test_reports_presence_and_does_what_the_cloud_asks(self):
        vigil_link.start()
        self.assertTrue(self.wait_for(vigil_link.connected))
        self.assertTrue(self.wait_for(lambda: vigil_link.unread() == 4))
        presence = [p for p in _FakeCloud.posts if p["method"] == "presence"][0]
        self.assertEqual(presence["kwargs"], {"idle": 7.0, "gil": True})
        _FakeCloud.events.put({"kind": "toast", "body": "Your exam moved", "urgency": "urgent"})
        _FakeCloud.events.put({"kind": "open_url", "url": "javascript:alert(1)"})   # never a program
        _FakeCloud.events.put({"kind": "send", "id": "c1", "text": "open spotify"})
        self.assertTrue(self.wait_for(lambda: any(p["method"] == "result" for p in _FakeCloud.posts)))
        self.assertEqual(self.sent, ["open spotify"])
        self.assertEqual(self.toasts[0][:3], ("G.I.L.", "Your exam moved", "urgent"))
        result = [p for p in _FakeCloud.posts if p["method"] == "result"][0]
        self.assertEqual(result["kwargs"], {"id": "c1", "result": True})

    def test_gils_replies_reach_the_cloud_before_done(self):
        vc._role = "link"

        def send(text):
            vc.gil_event("message", "Opening Spotify 🎵", "gil")
        vc._gil["send"] = send
        vigil_link.start()
        self.assertTrue(self.wait_for(vigil_link.connected))
        _FakeCloud.events.put({"kind": "send", "id": "c2", "text": "open spotify"})
        self.assertTrue(self.wait_for(lambda: any(p["method"] == "result" for p in _FakeCloud.posts)))
        order = [p["method"] for p in _FakeCloud.posts if p["method"] in ("gil_event", "result")]
        self.assertEqual(order, ["gil_event", "result"])
        ev = [p for p in _FakeCloud.posts if p["method"] == "gil_event"][0]
        self.assertEqual(ev["kwargs"], {"kind": "message", "args": ["Opening Spotify 🎵", "gil"]})

    def test_a_wrong_key_isnt_retried_fast(self):
        vigil_link.CONFIG.write_text(json.dumps(
            {"url": f"http://127.0.0.1:{self.srv.server_address[1]}", "key": "wrong"}),
            encoding="utf-8")
        vigil_link.start()
        time.sleep(0.5)
        self.assertFalse(vigil_link.connected())

    def test_not_configured_or_switched_off(self):
        self.assertTrue(vigil_link.configured())
        vigil_link.CONFIG.write_text(json.dumps({"url": "https://x", "key": "k", "enabled": False}),
                                     encoding="utf-8")
        self.assertFalse(vigil_link.configured())
        vigil_link.CONFIG.unlink()
        self.assertFalse(vigil_link.configured())


if __name__ == "__main__":
    unittest.main()
