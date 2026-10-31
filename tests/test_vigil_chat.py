"""Tests for vigil_chat.py — temp database, temp voice folder, temp watches,
and a faked model, toast, voice and idle clock: nothing touches the user's
real chat, inbox, speakers or desktop."""
import sys, os, json, tempfile, time, queue, threading, urllib.request
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from datetime import datetime
from pathlib import Path
import notify
import vigil
import vigil_chat as vc


class ChatCase(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig = {k: getattr(vc, k) for k in (
            "DB_PATH", "VOICE_DIR", "_conn", "idle_seconds", "synthesize",
            "transcribe", "_user")}
        self._orig_vigil = (vigil._PATH, vigil.call_json, vigil.run_order,
                            vigil._deliver_fn, vigil._status_fn)
        self._orig_toast = notify.toast
        vc.DB_PATH = self.tmp / "chat.db"
        vc.VOICE_DIR = self.tmp / "voice"
        vc._conn = None
        vc._user = "Omri"
        vigil._PATH = self.tmp / "vigil.json"
        self.idle = 0.0
        vc.idle_seconds = lambda: self.idle
        self.spoken = []

        def fake_synth(text):
            self.spoken.append(text)
            vc.VOICE_DIR.mkdir(parents=True, exist_ok=True)
            name = "gil-" + "a" * 12 + ".mp3"
            (vc.VOICE_DIR / name).write_bytes(b"ID3fake")
            return name
        vc.synthesize = fake_synth
        self.toasts = []
        notify.toast = lambda *a, **k: self.toasts.append((a, k)) or True
        self.checks = []
        vigil.run_order = lambda oid, force=False: self.checks.append(oid) or {}
        self.replies = []
        vigil.call_json = lambda *a, **k: (self.replies.pop(0) if self.replies else None, 100, "")
        vc._view.update(visible=False, focused=False, at=0.0)
        vc._away.update(since=0.0, voice=[])
        vc._activity.clear()
        while not vc._inbox.empty():
            vc._inbox.get_nowait()

    def tearDown(self):
        if vc._conn is not None:
            vc._conn.close()
        for k, v in self._orig.items():
            setattr(vc, k, v)
        (vigil._PATH, vigil.call_json, vigil.run_order,
         vigil._deliver_fn, vigil._status_fn) = self._orig_vigil
        notify.toast = self._orig_toast
        vc._view.update(visible=False, focused=False, at=0.0)

    def last(self, sender="gil"):
        return [m for m in vc.messages() if m["sender"] == sender][-1]

    def watch(self, title="Army mail"):
        return vigil.add(title, "watch it", [{"type": "gmail"}], active_hours=(0, 0))


class TestStore(ChatCase):

    def test_messages_come_back_in_order_with_meta_decoded(self):
        a = vc.add_message("gil", "first", meta={"watch": "x"})
        b = vc.add_message("me", "second", reply_to=a["id"])
        msgs = vc.messages()
        self.assertEqual([m["text"] for m in msgs], ["first", "second"])
        self.assertEqual(msgs[0]["meta"], {"watch": "x"})
        self.assertEqual(msgs[1]["quote"]["text"], "first")       # quoted inline
        self.assertEqual(b["status"], "sent")
        self.assertEqual(a["status"], "unread")

    def test_unread_and_read(self):
        vc.add_message("gil", "one")
        vc.add_message("gil", "two")
        self.assertEqual(vc.unread_count(), 2)
        self.assertEqual(vc.mark_read(), 2)
        self.assertEqual(vc.unread_count(), 0)

    def test_update_merges_meta_and_keeps_the_rest(self):
        m = vc.add_message("gil", "card", meta={"watch": "w", "buttons": [1]})
        vc.update_message(m["id"], resolved="Done ✓")
        got = vc.get_message(m["id"])
        self.assertEqual(got["meta"], {"watch": "w", "buttons": [1], "resolved": "Done ✓"})
        self.assertEqual(got["text"], "card")

    def test_paging_older_messages(self):
        ids = [vc.add_message("gil", str(i))["id"] for i in range(5)]
        self.assertEqual([m["text"] for m in vc.messages(limit=2)], ["3", "4"])
        self.assertEqual([m["text"] for m in vc.messages(limit=2, before=ids[3])], ["1", "2"])


class TestDelivery(ChatCase):

    def notice(self, **kw):
        n = {"id": "n1", "order_id": "o1", "title": "Army mail", "message": "The army emailed.",
             "urgency": "normal", "keep": False, "at": time.time()}
        n.update(kw)
        return n

    def test_at_the_pc_a_text_and_a_ping(self):
        vc.deliver(self.notice())
        m = self.last()
        self.assertEqual(m["kind"], "text")
        self.assertEqual(m["meta"]["watch"], "Army mail")
        self.assertEqual(len(self.toasts), 1)
        self.assertEqual(self.toasts[0][0][0], "G.I.L.")          # named like a contact

    def test_quiet_messages_never_ping(self):
        vc.deliver(self.notice(urgency="low"))
        self.assertEqual(self.toasts, [])
        self.assertEqual(self.last()["urgency"], "low")

    def test_away_from_the_pc_it_becomes_a_voice_message(self):
        self.idle = vc.AWAY_SECS + 5
        vc.deliver(self.notice(urgency="urgent"))
        m = self.last()
        self.assertEqual(m["kind"], "voice")
        self.assertTrue(m["audio"].startswith("/voice/gil-"))
        self.assertEqual(self.spoken, ["The army emailed."])
        self.assertEqual(m["text"], "The army emailed.")          # the transcript

    def test_away_but_quiet_stays_text(self):
        self.idle = vc.AWAY_SECS + 5
        vc.deliver(self.notice(urgency="low"))
        self.assertEqual(self.last()["kind"], "text")
        self.assertEqual(self.spoken, [])

    def test_needs_you_gets_its_buttons(self):
        vc.deliver(self.notice(keep=True))
        m = self.last()
        self.assertEqual(m["meta"]["needs_id"], "n1")
        self.assertEqual([b["id"] for b in m["meta"]["buttons"]], ["done", "later", "stop"])

    def test_no_ping_while_you_are_reading_the_chat(self):
        q = queue.Queue()
        vc._clients.append(q)
        vc._local_clients.add(q)
        try:
            vc.api_view(True, True)
            vc.deliver(self.notice())
            self.assertEqual(self.toasts, [])
        finally:
            vc._clients.remove(q)
            vc._local_clients.discard(q)

    def test_the_toast_opens_the_chat(self):
        vc.deliver(self.notice())
        self.assertIn("launch", self.toasts[0][1])

    def test_welcome_back_counts_unheard_voice_messages(self):
        self.idle = vc.AWAY_SECS + 5
        vc._away.update(since=time.time() - 700, voice=[])
        vc.deliver(self.notice())
        vc.deliver(self.notice(message="A second thing."))
        self.idle = 1
        vc._welcome_back()
        self.assertIn("2 voice messages", self.last()["text"])
        self.assertEqual(vc._away["voice"], [])

    def test_vigil_hands_its_messages_to_the_chat(self):
        vigil.set_deliver(vc.deliver)
        vigil._deliver(self.notice(message="via vigil"))
        self.assertEqual(self.last()["text"], "via vigil")


class TestQuickCommands(ChatCase):

    def send(self, text, reply_to=None):
        m = vc.add_message("me", text, reply_to=reply_to)
        vc._handle(m)
        return self.last()

    def test_stop_and_go(self):
        self.watch()
        self.assertIn("off watch", self.send("stop")["text"])
        self.assertFalse(vigil.master_on())
        self.assertIn("paused", vc.status_line())
        self.assertIn("watching again", self.send("Go!")["text"])
        self.assertTrue(vigil.master_on())
        self.assertEqual(vc.status_line(), "online")

    def test_hebrew_stop(self):
        self.send("תעצור")
        self.assertFalse(vigil.master_on())

    def test_stop_replying_to_a_watch_stops_only_that_watch(self):
        w = self.watch()
        msg = vc.add_message("gil", "army news", order_id=w["id"])
        reply = self.send("stop", reply_to=msg["id"])
        self.assertIn("Army mail", reply["text"])
        self.assertFalse(vigil.get(w["id"])["enabled"])
        self.assertTrue(vigil.master_on())                      # the rest keeps going

    def test_what_are_you_watching(self):
        self.watch("Army mail")
        self.assertIn("Army mail", self.send("what are you watching?")["text"])

    def test_your_message_gets_read_ticks(self):
        m = vc.add_message("me", "stop")
        vc._handle(m)
        self.assertEqual(vc.get_message(m["id"])["status"], "read")


class TestConversation(ChatCase):

    def converse(self, text, reply_to=None):
        m = vc.add_message("me", text, reply_to=reply_to)
        vc._handle(m)
        time.sleep(0.05)                                         # first-check thread
        return m

    def test_a_watch_set_up_by_chatting(self):
        self.replies = [{"reply": "On it 👀", "voice": False, "ops": [
            {"op": "create", "title": "HN agents", "brief": "AI agents on HN",
             "sources": [{"type": "url", "url": "https://news.ycombinator.com"}],
             "attention": "front-page stories about AI agents", "every_min": 30}]}]
        self.converse("watch hacker news for AI agents")
        orders = vigil.orders()
        self.assertEqual(len(orders), 1)
        msgs = vc.messages()
        self.assertEqual(msgs[-2]["text"], "On it 👀")
        self.assertEqual(msgs[-1]["kind"], "card")
        self.assertEqual(msgs[-1]["meta"]["watch"], "HN agents")
        self.assertEqual(self.checks, [orders[0]["id"]])         # a first look at once

    def test_less_like_this_rewrites_the_rule(self):
        w = self.watch("Python releases")
        msg = vc.add_message("gil", "Python 3.14.1 is out", order_id=w["id"])
        self.replies = [{"reply": "Got it.", "ops": [
            {"op": "tune", "id": w["id"], "attention": "major versions only"}]}]
        self.converse("less like this", reply_to=msg["id"])
        self.assertEqual(vigil.get(w["id"])["attention"], "major versions only")

    def test_what_it_cant_do_is_admitted(self):
        self.replies = [{"reply": "Sure.", "ops": [
            {"op": "create", "title": "x", "sources": [{"type": "whatsapp"}]},
            {"op": "pause", "id": "nope"}]}]
        self.converse("watch my whatsapp")
        text = self.last()["text"]
        self.assertIn("unknown source type", text)
        self.assertIn("couldn't find that watch", text)
        self.assertEqual(vigil.orders(), [])

    def test_a_failed_model_says_so(self):
        self.replies = []
        self.converse("hello?")
        self.assertIn("say that again", self.last()["text"])

    def test_voice_reply_when_asked(self):
        self.replies = [{"reply": "Here you go.", "voice": False, "ops": []}]
        self.converse("send me a voice message")
        self.assertEqual(self.last()["kind"], "voice")

    def test_live_questions_are_searched_not_answered_from_memory(self):
        import research
        orig = research.run
        research.run = lambda q: ("Long write-up\n\n**Sources:**\n- [A](https://a.example)\n"
                                  "- [B](https://b.example)\n- [C](https://c.example)",
                                  "It's 23 degrees and sunny.")
        try:
            self.converse("what's the weather in tel aviv today?")
        finally:
            research.run = orig
        text = self.last()["text"]
        self.assertTrue(text.startswith("It's 23 degrees"))     # the short answer
        self.assertIn("a.example", text)
        self.assertNotIn("c.example", text)                     # top two sources only
        self.assertNotIn("Long write-up", text)


class TestButtons(ChatCase):

    def needs_msg(self):
        w = self.watch()
        st = json.loads(vigil._PATH.read_text(encoding="utf-8"))
        st["inbox"] = [{"id": "n9", "order_id": w["id"], "title": "Army mail",
                        "message": "confirm", "urgency": "normal", "at": 0}]
        vigil._PATH.write_text(json.dumps(st), encoding="utf-8")
        return w, vc.add_message("gil", "confirm by Sunday", order_id=w["id"],
                                 meta={"watch": "Army mail", "needs_id": "n9",
                                       "buttons": vc._NEEDS_BUTTONS})

    def test_done_clears_it_from_your_list(self):
        _, m = self.needs_msg()
        out = vc.api_button(m["id"], "done")
        self.assertEqual(out["meta"]["resolved"], "Done ✓")
        self.assertEqual(vigil.needs_you(), [])

    def test_remind_me_later_comes_back(self):
        _, m = self.needs_msg()
        out = vc.api_button(m["id"], "later")
        self.assertIn("remind you at", out["meta"]["resolved"])
        with vc._db_lock:
            vc._db().execute("UPDATE reminders SET due=?", (time.time() - 1,))
            vc._db().commit()
        vc._due_reminders()
        again = self.last()
        self.assertTrue(again["text"].startswith("Reminder:"))
        self.assertEqual(again["reply_to"], m["id"])
        self.assertEqual(len(self.toasts), 1)

    def test_stop_these_pauses_the_watch(self):
        w, m = self.needs_msg()
        vc.api_button(m["id"], "stop")
        self.assertFalse(vigil.get(w["id"])["enabled"])

    def test_cancel_a_new_watch(self):
        w = self.watch()
        card = vc.add_message("gil", "card", kind="card", order_id=w["id"], meta={"watch": "Army mail"})
        vc.api_button(card["id"], "cancel_watch")
        self.assertIsNone(vigil.get(w["id"]))


class TestYourVoice(ChatCase):

    def test_a_voice_message_is_kept_transcribed_and_answered(self):
        vc.transcribe = lambda raw, name, mime: "what are you watching"
        m = vc.api_send_voice("data:audio/webm;base64,AAAA", "audio/webm;codecs=opus")
        self.assertEqual(m["kind"], "voice")
        self.assertTrue(m["meta"]["transcribing"])
        handed = vc._inbox.get(timeout=3)
        self.assertEqual(handed["text"], "what are you watching")
        self.assertTrue((vc.VOICE_DIR / m["audio"].split("/")[-1]).is_file())

    def test_unheard_voice_gets_a_kind_reply(self):
        vc.transcribe = lambda raw, name, mime: ""
        vc.api_send_voice("data:audio/webm;base64,AAAA")
        deadline = time.time() + 3
        while time.time() < deadline and "couldn't make that" not in (self.last()["text"] if
                [m for m in vc.messages() if m["sender"] == "gil"] else ""):
            time.sleep(0.05)
        self.assertIn("couldn't make that", self.last()["text"])

    def test_live_question_detection(self):
        self.assertTrue(vc._live_question("what's the weather in tel aviv today?"))
        self.assertTrue(vc._live_question("מה מזג האוויר בתל אביב היום"))
        self.assertFalse(vc._live_question("build me a weather app"))


class TestServer(ChatCase):

    def setUp(self):
        super().setUp()
        self._srv = (vc._httpd, vc._port, vc.PORTS)
        vc._httpd, vc._port, vc.PORTS = None, 0, (0,)
        vc._start_server()
        self.base = vc.url()

    def tearDown(self):
        vc._httpd.shutdown()
        vc._httpd.server_close()
        vc._httpd, vc._port, vc.PORTS = self._srv
        super().tearDown()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path.lstrip("/"), timeout=5) as r:
                return r.status, r.headers.get("Content-Type"), r.read()
        except urllib.error.HTTPError as e:
            return e.code, None, b""

    def test_serves_the_page(self):
        status, ctype, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", ctype)
        self.assertIn(b"Contact info", body)

    def test_voice_files_and_nothing_else(self):
        vc.VOICE_DIR.mkdir(parents=True, exist_ok=True)
        (vc.VOICE_DIR / ("gil-" + "b" * 12 + ".mp3")).write_bytes(b"ID3")
        status, ctype, _ = self.get("/voice/gil-" + "b" * 12 + ".mp3")
        self.assertEqual((status, ctype), (200, "audio/mpeg"))
        for bad in ("/voice/..%2F..%2Fvigil.json", "/voice/gil-x.mp3", "/voice/notes.txt"):
            self.assertEqual(self.get(bad)[0], 404, bad)

    def test_the_api(self):
        vc.add_message("gil", "hello")
        req = urllib.request.Request(self.base + "api", method="POST",
                                     data=json.dumps({"method": "state", "args": []}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "X-Vigil": "1"})
        with urllib.request.urlopen(req, timeout=5) as r:
            state = json.loads(r.read())["result"]
        self.assertEqual(state["messages"][-1]["text"], "hello")
        self.assertIn("watches", state)
        self.assertIn("suggestions", state)


class TestVigilAdditions(ChatCase):

    def test_update_changes_a_watch(self):
        w = self.watch()
        vigil.update(w["id"], attention="only urgent", every_min=1, title=None, bogus="x")
        got = vigil.get(w["id"])
        self.assertEqual(got["attention"], "only urgent")
        self.assertEqual(got["every_min"], vigil.MIN_EVERY_MIN)    # floored
        self.assertNotIn("bogus", got)
        with self.assertRaises(ValueError):
            vigil.update(w["id"], at="25:99")

    def test_master_off_runs_nothing(self):
        self.watch()
        vigil.set_master(False)
        self.assertIsNone(vigil.tick())

    def test_a_daily_routine_runs_once_at_its_time(self):
        o = vigil.add("Brief", "b", [{"type": "gmail"}], at="08:00")
        today = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        self.assertFalse(vigil._is_due(o, today - 60))
        self.assertTrue(vigil._is_due(o, today + 1800))
        o["last_run"] = today + 1800
        self.assertFalse(vigil._is_due(o, today + 3600))           # once a day
        self.assertTrue(vigil._is_due(o, today + 86400 + 60))      # and again tomorrow

    def test_bad_daily_time_rejected(self):
        with self.assertRaises(ValueError):
            vigil.add("x", "x", [{"type": "gmail"}], at="8am")

    def test_retry_waits(self):
        o = self.watch()
        o["retry_at"] = time.time() + 100
        self.assertFalse(vigil._is_due(o, time.time()))

    def test_checking_label(self):
        o = vigil.add("x", "x", [{"type": "gmail"}, {"type": "url", "url": "https://python.org/x"}])
        self.assertEqual(vigil._checking_label(o), "checking your inbox and python.org…")


class TestOpeningVigil(unittest.TestCase):
    """'open vigil' opens it; a sentence that only mentions Vigil doesn't."""

    def test_the_phrase(self):
        import fast_path_handler as fp
        opened, said = [], []
        orig = vc.open_window
        vc.open_window = lambda: opened.append(1) or True

        class Eng:
            class window:
                @staticmethod
                def add_chat_message(text, who):
                    said.append(text)
        try:
            for phrase in ("open vigil", "Vigil", "vigil mode", "activate vigil!",
                           "תפתח את ויג'יל", "פתח את ויגיל"):
                self.assertTrue(fp._vigil_open(phrase, phrase.lower(), Eng), phrase)
            for phrase in ("a candlelight vigil", "open spotify",
                           "what is the vigil feature you built and how does it work for me"):
                self.assertFalse(fp._vigil_open(phrase, phrase.lower(), Eng), phrase)
        finally:
            vc.open_window = orig
        self.assertEqual(len(opened), 6)
        self.assertIn("Opening Vigil", said[0])


class TestServerGuards(TestServer):
    """Only this PC, or a paired phone, and never another website."""

    def post(self, headers, method="state"):
        req = urllib.request.Request(self.base + "api", method="POST",
                                     data=json.dumps({"method": method, "args": []}).encode(),
                                     headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_a_post_without_the_header_is_refused(self):
        # what a form or a fetch from another site in the browser would send
        self.assertEqual(self.post({"Content-Type": "text/plain"}), 403)
        self.assertEqual(self.post({"X-Vigil": "1"}), 200)

    def test_another_domain_pointed_here_is_refused(self):
        # DNS rebinding: evil.example resolving to 127.0.0.1
        self.assertEqual(self.post({"X-Vigil": "1", "Host": f"evil.example:{vc._port}"}), 403)
        req = urllib.request.Request(self.base, headers={"Host": "evil.example"})
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=5)

    def test_the_manifest(self):
        status, ctype, body = self.get("/vigil.webmanifest")
        self.assertEqual(status, 200)
        manifest = json.loads(body)
        self.assertEqual(manifest["short_name"], "G.I.L.")
        self.assertEqual({i["sizes"] for i in manifest["icons"]}, {"192x192", "512x512"})
        status, ctype, body = self.get("/sw.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", ctype)
        self.assertIn(b"notificationclick", body)


class _FakeRequest:
    """Just enough of a request for the handler's access rules."""

    def __init__(self, ip, headers=None, path="/api"):
        self.client_address = (ip, 5000)
        self.headers = {"Host": "127.0.0.1:8741", **(headers or {})}
        self.path = path

    def _host(self):
        return vc._Handler._host(self)

    def _local(self):
        return vc._Handler._local(self)


TS_NAME = "omri-pc.tail1234.ts.net"


class TestPhone(ChatCase):
    """Phone access: through Tailscale (G.I.L.'s app, anywhere) or this Wi-Fi
    only. A phone always needs the key; turning it off forgets everything."""

    def setUp(self):
        super().setUp()
        import mobile, tailscale_link, phone_push
        self.ts, self.pp = tailscale_link, phone_push
        self._orig_phone = (vc._rebind, vc._port, mobile.lan_ip, tailscale_link.status,
                            tailscale_link.serve, tailscale_link.unserve, phone_push.SUBS_FILE)
        self.binds, self.unserved = [], []
        vc._rebind = self.binds.append
        vc._port = 8741
        mobile.lan_ip = lambda: "192.168.1.20"
        self.ts_state = {"installed": True, "running": True, "name": TS_NAME, "https": True, "why": ""}
        tailscale_link.status = lambda fresh=False: dict(self.ts_state)
        self.serve_result = {"ok": True, "url": f"https://{TS_NAME}/", "enable_url": "", "why": ""}
        tailscale_link.serve = lambda port: dict(self.serve_result)
        tailscale_link.unserve = lambda: self.unserved.append(1)
        phone_push.SUBS_FILE = self.tmp / "push.json"

    def tearDown(self):
        import mobile
        (vc._rebind, vc._port, mobile.lan_ip, self.ts.status, self.ts.serve, self.ts.unserve,
         self.pp.SUBS_FILE) = self._orig_phone
        super().tearDown()

    def ok(self, ip, headers=None, path="/api"):
        return vc._Handler._token_ok(_FakeRequest(ip, headers, path))

    def host_ok(self, ip, headers):
        return vc._Handler._host_ok(_FakeRequest(ip, headers))

    def through_tailscale(self, extra=None):
        """What Tailscale's HTTPS proxy sends on: from 127.0.0.1, but forwarded."""
        return {"Host": TS_NAME, "X-Forwarded-For": "100.101.102.103",
                "Tailscale-User-Login": "omri@example.com", **(extra or {})}

    def test_off_by_default_and_the_network_is_shut_out(self):
        self.assertFalse(vc._phone()["on"])
        self.assertTrue(self.ok("127.0.0.1"))
        self.assertFalse(self.ok("192.168.1.30", {"X-GIL-Token": "anything"}))
        self.assertFalse(self.host_ok("127.0.0.1", self.through_tailscale()))

    def test_tailscale_gives_an_https_app_link(self):
        view = vc.api_phone(True)
        token = vc._phone()["token"]
        self.assertTrue(view["on"])
        self.assertEqual(view["via"], "tailscale")
        self.assertEqual(view["url"], f"https://{TS_NAME}/?t={token}")
        self.assertEqual(self.binds, ["127.0.0.1"])                     # never opened to the LAN
        self.assertGreaterEqual(len(token), 20)
        self.assertNotIn(token, json.dumps(vc._state_extras()))        # state never carries it
        self.assertEqual(vc._state_extras()["phone"]["via"], "tailscale")

    def test_a_request_through_tailscale_is_a_phone_not_this_pc(self):
        vc.api_phone(True)
        token = vc._phone()["token"]
        self.assertTrue(self.host_ok("127.0.0.1", self.through_tailscale()))
        self.assertFalse(vc._Handler._local(_FakeRequest("127.0.0.1", self.through_tailscale())))
        self.assertFalse(self.ok("127.0.0.1", self.through_tailscale()))                 # no key
        self.assertTrue(self.ok("127.0.0.1", self.through_tailscale({"X-GIL-Token": token})))
        self.assertFalse(self.host_ok("127.0.0.1", {"Host": "evil.example"}))
        self.assertTrue(vc._Handler._local(_FakeRequest("127.0.0.1")))                # the PC itself

    def test_tailscale_not_ready_explains_and_opens_nothing(self):
        self.ts_state.update(running=False, name="", why="Tailscale isn't installed on this PC")
        self.serve_result = {"ok": False, "url": "", "enable_url": "", "why": ""}
        view = vc.api_phone(True)
        self.assertFalse(view["on"])
        self.assertTrue(view["setup"])
        self.assertEqual(view["why"], "Tailscale isn't installed on this PC")
        self.assertEqual(self.binds, [])
        self.assertFalse(vc._phone()["on"])

    def test_https_switch_link_is_passed_on(self):
        self.serve_result = {"ok": False, "url": "", "why": "HTTPS isn't on for your tailnet yet",
                             "enable_url": "https://login.tailscale.com/f/serve?node=n1"}
        view = vc.api_phone(True)
        self.assertEqual(view["enable_url"], "https://login.tailscale.com/f/serve?node=n1")

    def test_home_wifi_fallback(self):
        view = vc.api_phone(True, True)
        token = vc._phone()["token"]
        self.assertEqual(view["via"], "wifi")
        self.assertEqual(self.binds, ["0.0.0.0"])
        self.assertEqual(view["url"], f"http://192.168.1.20:8741/?t={token}")
        self.assertTrue(self.ok("192.168.1.30", {"X-GIL-Token": token}))
        self.assertTrue(self.ok("192.168.1.30", {}, f"/events?t={token}"))
        self.assertFalse(self.ok("192.168.1.30", {"X-GIL-Token": token[:-1] + "x"}))
        self.assertTrue(self.host_ok("192.168.1.30", {"Host": "192.168.1.20:8741"}))

    def test_turning_it_off_forgets_the_key_and_the_phones(self):
        vc.api_phone(True)
        token = vc._phone()["token"]
        self.pp.subscribe({"endpoint": "https://fcm.googleapis.com/fcm/send/x",
                           "keys": {"p256dh": "k", "auth": "a"}}, "Pixel")
        vc.api_phone(False)
        self.assertEqual(self.unserved, [1])
        self.assertEqual(self.pp.count(), 0)
        self.assertFalse(self.ok("127.0.0.1", self.through_tailscale({"X-GIL-Token": token})))
        vc.api_phone(True)
        self.assertNotEqual(vc._phone()["token"], token)

    def test_a_phone_cant_open_phone_access_or_windows(self):
        self.assertEqual(vc._LOCAL_ONLY, {"phone", "open"})


class TestPhoneNotifications(ChatCase):

    def setUp(self):
        super().setUp()
        import phone_push
        self.pp = phone_push
        self._orig_pp = (phone_push.SUBS_FILE, phone_push.send)
        phone_push.SUBS_FILE = self.tmp / "push.json"
        self.pushed = []
        phone_push.send = lambda payload, urgency="normal", ttl=0: self.pushed.append((payload, urgency)) or 1
        phone_push.subscribe({"endpoint": "https://fcm.googleapis.com/fcm/send/x",
                              "keys": {"p256dh": "k", "auth": "a"}}, "Pixel")

    def tearDown(self):
        self.pp.SUBS_FILE, self.pp.send = self._orig_pp
        super().tearDown()

    def wait_push(self, n=1):
        deadline = time.time() + 2
        while len(self.pushed) < n and time.time() < deadline:
            time.sleep(0.02)

    def test_a_ping_reaches_the_phone(self):
        msg = vc.add_message("gil", "The army emailed you", urgency="urgent", meta={"watch": "Army mail"})
        vc._ping(msg)
        self.wait_push()
        payload, urgency = self.pushed[0]
        self.assertEqual(payload["title"], "G.I.L. · Army mail")
        self.assertEqual(payload["body"], "The army emailed you")
        self.assertEqual(payload["tag"], f"msg-{msg['id']}")
        self.assertEqual(urgency, "high")

    def test_a_can_i_card_carries_its_buttons(self):
        msg = vc.add_message("gil", "Remind you about the form", kind="card",
                             meta={"card": "action", "buttons": [{"id": "approve", "label": "Do it"},
                                                                 {"id": "deny", "label": "Don't"},
                                                                 {"id": "always", "label": "Always"}]})
        vc._ping(msg)
        self.wait_push()
        payload = self.pushed[0][0]
        self.assertEqual(payload["body"], "Can I? Remind you about the form")
        self.assertEqual([a["action"] for a in payload["actions"]], ["approve", "deny"])

    def test_away_only_and_off(self):
        vc.api_phone_notify("away")
        vc._ping(vc.add_message("gil", "at the PC"))
        time.sleep(0.1)
        self.assertEqual(self.pushed, [])
        self.idle = vc.AWAY_SECS + 5
        vc._ping(vc.add_message("gil", "away now"))
        self.wait_push()
        self.assertEqual(self.pushed[0][0]["body"], "away now")
        vc.api_phone_notify("off")
        vc._ping(vc.add_message("gil", "not at all"))
        time.sleep(0.1)
        self.assertEqual(len(self.pushed), 1)
        vc.api_phone_notify("sometimes")                                # ignored
        self.assertEqual(vc._state_extras()["phone"]["notify"], "off")

    def test_no_push_while_youre_reading(self):
        q = queue.Queue()
        vc._clients.append(q)
        vc._local_clients.add(q)
        vc.api_view(True, True)
        try:
            vc._ping(vc.add_message("gil", "hi"))
        finally:
            vc._clients.remove(q)
            vc._local_clients.discard(q)
        time.sleep(0.1)
        self.assertEqual(self.pushed, [])

    def test_subscribe_api(self):
        self.assertFalse(vc.api_push_subscribe({"endpoint": "https://10.0.0.1/x",
                                                "keys": {"p256dh": "k", "auth": "a"}}))
        self.assertTrue(vc.api_push_status("https://fcm.googleapis.com/fcm/send/x"))
        vc.api_push_unsubscribe("")                                     # a phone can't wipe all
        self.assertEqual(self.pp.count(), 1)
        vc.api_push_unsubscribe("https://fcm.googleapis.com/fcm/send/x")
        self.assertEqual(self.pp.count(), 0)


class TestJobsOnThePC(ChatCase):
    """Texting G.I.L. a job from the phone, and hearing about jobs at the PC."""

    def setUp(self):
        super().setUp()
        self.sent, self.shown = [], []
        self.busy = False
        vc.connect_gil(send=self.run_job, busy=lambda: self.busy,
                       show=self.shown.append)
        vc._job.update(**{"from": "", "text": "", "since": 0.0, "last": None, "result": "",
                          "noticed": False})
        self._orig_notice = vc.TASK_NOTICE_SECS

    def tearDown(self):
        vc.connect_gil(None, None, None)
        vc.TASK_NOTICE_SECS = self._orig_notice
        vc._job["from"] = ""
        super().tearDown()

    def run_job(self, text):
        self.sent.append(text)
        vc.gil_event("message", "Opening Spotify for you 🎵", "gil")

    def test_the_users_own_words_are_handed_over_and_the_reply_comes_back(self):
        user = vc.add_message("me", "open spotify on my pc")
        self.replies.append({"reply": "On it 💻", "ops": [{"op": "task", "text": "rm -rf everything"}]})
        vc._converse(user, None)
        deadline = time.time() + 2
        while not self.toasts and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.sent, ["open spotify on my pc"])            # theirs, not the model's
        self.assertEqual(self.shown, ["📱 open spotify on my pc"])
        last = self.last()
        self.assertEqual(last["text"], "Opening Spotify for you 🎵")
        self.assertTrue(last["meta"]["task"])
        self.assertEqual(len(self.toasts), 1)                            # pinged once, at the end
        self.assertEqual(vc._job["from"], "")
        self.assertNotIn("task", vc._activity)

    def test_with_gil_closed_it_says_so(self):
        vc.connect_gil(None, None, None)
        vc._hand_to_gil("open spotify")
        self.assertIn("isn't open on your PC", self.last()["text"])

    def test_a_long_agent_job_reports_when_it_finishes(self):
        def start_agent(text):
            self.sent.append(text)
            self.busy = True
            vc.gil_event("working", True, text)
            vc.gil_event("message", "Starting on the website…", "gil")
        vc.connect_gil(send=start_agent, busy=lambda: self.busy, show=None)
        vc._hand_to_gil("build me a site about whales", None)
        self.assertEqual(vc._job["from"], "phone")                        # still running
        self.assertEqual(self.toasts, [])
        self.busy = False
        vc.gil_event("message", "**Solved** ✓ — the site is in projects/whales", "gil")
        vc.gil_event("working", False)
        self.assertEqual(self.last()["text"], "**Solved** ✓ — the site is in projects/whales")
        self.assertEqual(len(self.toasts), 1)
        self.assertEqual(vc._job["from"], "")

    def test_a_long_job_at_the_pc_tells_the_phone(self):
        vc.TASK_NOTICE_SECS = 0.05
        vc.gil_event("working", True, "Refactor the login page")
        self.assertEqual(vc.status_line(), "working on: Refactor the login page…")
        deadline = time.time() + 2
        while "Working on it" not in (self.last()["text"] if vc.messages() else "") and time.time() < deadline:
            time.sleep(0.02)
        self.assertIn("Working on it on your PC: Refactor the login page", self.last()["text"])
        self.assertEqual(self.toasts, [])                                  # quiet
        vc.gil_event("message", "**Solved** ✓ — login page tidied", "gil")
        vc.gil_event("working", False)
        done = self.last()
        self.assertTrue(done["text"].startswith("Done ✓ Refactor the login page"))
        self.assertIn("login page tidied", done["text"])
        self.assertEqual(len(self.toasts), 1)

    def test_a_short_job_at_the_pc_stays_quiet(self):
        vc.gil_event("working", True, "quick fix")
        vc.gil_event("working", False)
        self.assertEqual(vc.messages(), [])
        self.assertEqual(self.toasts, [])

    def test_a_broken_hook_never_breaks_gil(self):
        vc.gil_event("message")                                          # missing args
        vc.gil_event("nonsense", 1, 2, 3)


class TestActions(ChatCase):
    """What Vigil may do, and the gates in front of it."""

    def setUp(self):
        super().setUp()
        import vigil_actions as va
        self.va = va
        self._orig_va = (va.execute, va.sentinel, va.asked_for)
        self.done = []
        va.execute = lambda a: self.done.append(a) or (True, "All set.")
        self.verdict = (True, "they asked for this")
        va.sentinel = lambda a, o: self.verdict
        self.asked = True
        va.asked_for = lambda a, m: self.asked
        self.later = (datetime.now().replace(second=0, microsecond=0)
                      + __import__("datetime").timedelta(days=1)).isoformat(timespec="minutes")

    def tearDown(self):
        self.va.execute, self.va.sentinel, self.va.asked_for = self._orig_va
        super().tearDown()

    def order(self, actions=("reminder",)):
        return vigil.add("Uni", "uni mail", [{"type": "gmail"}], active_hours=(0, 0),
                         actions=list(actions), asked="remind me about deadlines")

    def remind(self):
        return {"type": "reminder", "text": "Submit the form", "when": self.later,
                "why": "deadline tomorrow"}

    # validate / describe
    def test_validate(self):
        ok, _ = self.va.validate(self.remind())
        self.assertEqual(ok["text"], "Submit the form")
        self.assertIsNone(self.va.validate({"type": "reminder", "text": "x",
                                            "when": "2001-01-01T09:00"})[0])
        self.assertIsNone(self.va.validate({"type": "send_email"})[0])
        self.assertIsNone(self.va.validate({"type": "open_page", "url": "http://127.0.0.1:8741/"})[0])
        self.assertIsNone(self.va.validate({"type": "open_page", "url": "file:///C:/x"})[0])
        cal, _ = self.va.validate({"type": "calendar_event", "title": "Exam",
                                   "start": self.later, "minutes": 99999})
        self.assertEqual(cal["minutes"], 1440)
        self.assertIn("tomorrow", self.va.describe(cal))

    def test_sentinel_fails_closed(self):
        self.va.sentinel = self._orig_va[1]
        vigil.call_json = lambda *a, **k: (None, 0, "rate limited")
        self.assertEqual(self.va.sentinel(self.va.validate(self.remind())[0], self.order())[0], False)

    def test_sentinel_reads_only_the_users_words(self):
        self.va.sentinel = self._orig_va[1]
        seen = []
        vigil.call_json = lambda msgs, *a, **k: (seen.append(msgs[-1]["content"]) or
                                                 {"allow": True}, 50, "")
        o = self.order()
        self.assertTrue(self.va.sentinel(self.va.validate(self.remind())[0], o)[0])
        self.assertIn("remind me about deadlines", seen[0])
        self.assertIn("Submit the form", seen[0])

    def test_direct_check_fails_closed(self):
        self.va.asked_for = self._orig_va[2]
        vigil.call_json = lambda *a, **k: (None, 0, "down")
        self.assertFalse(self.va.asked_for(self.remind(), "remind me tomorrow"))
        self.assertFalse(self.va.asked_for(self.remind(), ""))

    # an order's own idea: sentinel, then the user's level
    def test_ask_first_is_a_card(self):
        o = self.order()
        vc._propose(self.remind(), o["id"], None)
        card = self.last()
        self.assertEqual(card["meta"]["card"], "action")
        self.assertEqual([b["id"] for b in card["meta"]["buttons"]], ["approve", "deny", "always"])
        self.assertEqual(self.done, [])
        self.assertTrue(self.toasts[-1][0][1].startswith("Can I?"))

    def test_just_do_it(self):
        self.va.set_level("reminder", "auto")
        vc._propose(self.remind(), self.order()["id"], None)
        self.assertEqual(len(self.done), 1)
        self.assertTrue(self.last()["text"].startswith("Done:"))

    def test_never_only_tells(self):
        self.va.set_level("reminder", "never")
        vc._propose(self.remind(), self.order()["id"], None)
        self.assertEqual(self.done, [])
        self.assertTrue(self.last()["text"].startswith("You might want to"))

    def test_sentinel_says_no(self):
        self.va.set_level("reminder", "auto")
        self.verdict = (False, "they only asked to be told")
        vc._propose(self.remind(), self.order()["id"], None)
        self.assertEqual(self.done, [])
        self.assertTrue(self.last("system")["text"].startswith("Blocked:"))

    def test_approve_once_only(self):
        vc._propose(self.remind(), self.order()["id"], None)
        card = self.last()
        out = vc.api_button(card["id"], "approve")
        self.assertTrue(out["meta"]["resolved"].startswith("Done ✓"))
        vc.api_button(card["id"], "approve")
        self.assertEqual(len(self.done), 1)

    def test_deny_and_always(self):
        o = self.order()
        vc._propose(self.remind(), o["id"], None)
        self.assertEqual(vc.api_button(self.last()["id"], "deny")["meta"]["resolved"], "Not done")
        vc._propose(self.remind(), o["id"], None)
        vc.api_button(self.last()["id"], "always")
        self.assertEqual(self.va.level("reminder"), "auto")
        self.assertEqual(len(self.done), 1)

    def test_a_card_whose_time_passed(self):
        vc._propose(self.remind(), self.order()["id"], None)
        card = self.last()
        vc.update_message(card["id"], action={**card["meta"]["action"], "when": "2001-01-01T09:00"})
        out = vc.api_button(card["id"], "approve")
        self.assertTrue(out["meta"]["resolved"].startswith("Not done"))
        self.assertEqual(self.done, [])

    # the user's own request in the chat
    def converse(self, text, ops):
        self.replies.append({"reply": "On it", "ops": ops})
        vc._converse(vc.add_message("me", text), None)

    def test_asked_in_the_chat_it_just_happens(self):
        self.converse("remind me tomorrow to submit the form",
                      [{"op": "act", "action": self.remind()}])
        self.assertEqual(len(self.done), 1)

    def test_not_sure_they_asked_it_asks(self):
        self.asked = False
        self.converse("ok", [{"op": "act", "action": self.remind()}])
        self.assertEqual(self.done, [])
        self.assertEqual(self.last()["meta"]["card"], "action")

    def test_loosening_needs_a_tap(self):
        self.converse("you can set reminders without asking",
                      [{"op": "allow", "type": "reminder", "level": "auto"}])
        self.assertEqual(self.va.level("reminder"), "ask")
        card = self.last()
        self.assertEqual(card["meta"]["card"], "allow")
        vc.api_button(card["id"], "allow_yes")
        self.assertEqual(self.va.level("reminder"), "auto")

    def test_tightening_just_happens(self):
        self.va.set_level("reminder", "auto")
        self.converse("always ask me first", [{"op": "allow", "type": "reminder", "level": "ask"}])
        self.assertEqual(self.va.level("reminder"), "ask")

    def test_a_watch_keeps_their_words_and_its_powers(self):
        self.converse("watch uni mail and put exams in my calendar",
                      [{"op": "create", "title": "Uni", "brief": "uni mail",
                        "sources": [{"type": "gmail"}], "actions": ["calendar_event"]}])
        o = vigil.orders()[-1]
        self.assertEqual(o["actions"], ["calendar_event"])
        self.assertEqual(o["asked"], "watch uni mail and put exams in my calendar")

    def test_abilities_in_the_contact_page(self):
        vc.api_ability("open_page", "never")
        rows = {a["type"]: a for a in vc._state_extras()["abilities"]}
        self.assertEqual(rows["open_page"]["level"], "never")
        self.assertEqual([lv["id"] for lv in rows["reminder"]["levels"]], ["auto", "ask", "never"])
        vc.api_ability("open_page", "bogus")                 # ignored
        self.assertEqual(self.va.level("open_page"), "never")

    def test_reminders_arrive_on_time(self):
        vc.schedule_reminder("Call grandma", self.later)
        vc._due_reminders()
        self.assertFalse([m for m in vc.messages() if "Call grandma" in m["text"]])
        with vc._db_lock:
            vc._db().execute("UPDATE later SET due=?", (time.time() - 1,))
            vc._db().commit()
        vc._due_reminders()
        self.assertEqual(self.last()["text"], "⏰ Call grandma")
        vc._due_reminders()                                  # once
        self.assertEqual(len([m for m in vc.messages() if "Call grandma" in m["text"]]), 1)


class TestHosting(unittest.TestCase):
    """One process runs Vigil; the other only opens it."""

    def setUp(self):
        self._orig = (vc.HOST_MUTEX, vc._mutex, vc._role, vc.PORT_FILE, vc._raise_window,
                      vc._protocol_ok)
        vc.HOST_MUTEX = f"ProjectGIL_VigilHost_test_{os.getpid()}_{time.time_ns()}"
        vc._mutex, vc._role = None, ""
        vc._raise_window = lambda: False
        vc.PORT_FILE = Path(tempfile.mkdtemp()) / "port.txt"

    def tearDown(self):
        if vc._mutex:
            import ctypes
            ctypes.windll.kernel32.CloseHandle(vc._mutex)
        (vc.HOST_MUTEX, vc._mutex, vc._role, vc.PORT_FILE, vc._raise_window,
         vc._protocol_ok) = self._orig

    def test_only_one_host(self):
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.CreateMutexW.restype = wintypes.HANDLE
        other = k32.CreateMutexW(None, False, vc.HOST_MUTEX)        # someone else hosts
        try:
            self.assertFalse(vc._claim_host())
            self.assertEqual(vc.start(), "client")
        finally:
            k32.CloseHandle(other)
        vc._role = ""
        self.assertTrue(vc._claim_host())                             # they left: ours now

    def test_a_client_asks_the_host_to_open_the_window(self):
        vc._role = "client"
        self.assertFalse(vc.open_window())                            # nobody to ask
        opened = []
        orig = (vc._httpd, vc._port, vc.PORTS, vc.open_window)
        vc._httpd, vc._port, vc.PORTS = None, 0, (0,)
        vc._start_server()
        vc.PORT_FILE.write_text(str(vc._port), encoding="utf-8")
        try:
            vc.open_window = lambda: opened.append(1) or True         # the host's side
            self.assertTrue(vc.open_from_outside())
            self.assertEqual(opened, [1])
        finally:
            vc._httpd.shutdown()
            vc._httpd.server_close()
            vc._httpd, vc._port, vc.PORTS, vc.open_window = orig

    def test_the_notification_link(self):
        import types
        writes = {}

        class Key:
            def __init__(self, path):
                self.path = path

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        fake = types.SimpleNamespace(
            HKEY_CURRENT_USER="HKCU", REG_SZ=1,
            CreateKey=lambda root, path: Key(path),
            SetValueEx=lambda key, name, _r, _t, value: writes.__setitem__((key.path, name), value))
        orig = sys.modules.get("winreg")
        sys.modules["winreg"] = fake
        try:
            vc._register_protocol()
            cmd = writes[(r"Software\Classes\gil-vigil\shell\open\command", "")]
            self.assertIn("vigil_open.py", cmd)
            self.assertTrue(cmd.endswith('"%1"'))
            self.assertEqual(writes[(r"Software\Classes\gil-vigil", "URL Protocol")], "")
            sys.frozen = True
            vc._register_protocol()
            cmd = writes[(r"Software\Classes\gil-vigil\shell\open\command", "")]
            self.assertIn("--open-vigil", cmd)
        finally:
            if hasattr(sys, "frozen"):
                del sys.frozen
            sys.modules["winreg"] = orig
        self.assertEqual(vc._launch_url(), "gil-vigil:open")


class TestYourMail(ChatCase):
    """Reading Gmail any time back: answers with the emails linked, notes
    first when there's too much for one call, and a dead login said plainly."""

    def setUp(self):
        super().setUp()
        import gmail_reader
        self.gr = gmail_reader
        self._orig_gr = (gmail_reader.search, gmail_reader.reconnect)
        self.searched = []
        self.found = [self.mail(i) for i in range(1, 4)]

        def fake_search(query, limit=40, body_chars=1500):
            self.searched.append(query)
            if isinstance(self.found, Exception):
                raise self.found
            return list(self.found), len(self.found)
        gmail_reader.search = fake_search
        vc._last_mail.clear()

    def tearDown(self):
        self.gr.search, self.gr.reconnect = self._orig_gr
        vc._last_mail.clear()
        super().tearDown()

    def mail(self, i, body="Your exam is on 12 November at 08:00."):
        return {"id": f"m{i}", "thread": f"t{i}", "at": time.time() - i * 86400, "from": "IDF",
                "to": "Omri", "subject": f"Exam [{i}]", "body": body, "snippet": body[:40],
                "link": f"https://mail.google.com/mail/u/0/#all/t{i}"}

    def ask(self, question="when is my exam?", **kw):
        user = vc.add_message("me", question)
        vc._read_mail(kw.pop("query", "from:idf exam"), question, kw.pop("broader", ""), user)
        return user

    def test_an_answer_with_its_emails_linked(self):
        self.replies.append({"reply": "The IDF wrote that your exam is on 12 November at 08:00.",
                             "cites": [1, 9, "x", 1], "draft": ""})
        user = self.ask()
        got = self.last()
        self.assertTrue(got["text"].startswith("The IDF wrote that your exam is on 12 November"))
        self.assertIn("📧 [Exam 1 — IDF,", got["text"])                  # brackets can't break the link
        self.assertIn("(https://mail.google.com/mail/u/0/#all/t1)", got["text"])
        self.assertEqual(got["text"].count("📧"), 1)                     # bad and repeated cites dropped
        self.assertEqual(got["reply_to"], user["id"])
        self.assertEqual(got["meta"]["mail"], {"query": "from:idf exam", "read": 3, "total": 3})
        self.assertEqual(vc._last_mail["query"], "from:idf exam")
        self.assertEqual(vc._activity, {})                               # status cleared

    def test_links_from_emails_never_reach_the_chat(self):
        self.replies.append({"reply": "Confirm at [this page](https://phish.example/x) or https://evil.example",
                             "cites": [], "draft": ""})
        self.ask()
        text = self.last()["text"]
        self.assertNotIn("phish.example", text)
        self.assertNotIn("evil.example", text)

    def test_a_draft_comes_as_its_own_message(self):
        self.replies.append({"reply": "Here's a draft — I haven't sent anything.", "cites": [1],
                             "draft": "Hi, I confirm I'll be there on 12 November."})
        self.ask("draft a reply confirming")
        draft = self.last()
        self.assertTrue(draft["meta"]["draft"])
        self.assertEqual(draft["text"], "Hi, I confirm I'll be there on 12 November.")

    def test_a_draft_pasted_into_the_reply_too_appears_once(self):
        d = "שלום,\nאני מאשר את הגעתי לבחינה ביום חמישי"
        self.replies.append({"reply": f"הנה טיוטה לתשובה:\n\ndraft\n{d}\n\n(הערה: לא נשלח)",
                             "cites": [1], "draft": d})
        self.ask("draft a reply confirming")
        answer = [m for m in vc.messages() if m["sender"] == "gil"][-2]
        self.assertTrue(answer["text"].startswith("הנה טיוטה לתשובה — לא שלחתי כלום."))
        self.assertNotIn("אני מאשר", answer["text"])
        self.assertIsNone(self.last()["reply_to"])

    def test_nothing_found_tries_the_broader_search_then_says_so(self):
        self.found = []
        self.ask(query="from:idf subject:exam", broader="idf")
        self.assertEqual(self.searched, ["from:idf subject:exam", "idf"])
        self.assertIn("I didn't find any emails for that", self.last()["text"])

    def test_a_dead_login_offers_to_reconnect(self):
        self.found = self.gr.MailboxError(self.gr.NOT_CONNECTED)
        self.ask()
        got = self.last()
        self.assertIn("I can't read your Gmail right now — Gmail isn't connected", got["text"])
        self.assertEqual(got["meta"]["buttons"][0]["id"], "reconnect_gmail")

    def test_too_much_for_one_call_reads_in_batches(self):
        self.found = [self.mail(i, body="x" * 3000) for i in range(1, 8)]
        prompts = []

        def fake_call(messages, must, **kw):
            prompts.append((must, messages[-1]["content"]))
            if must == "notes":
                return {"notes": "exam moved to 14 Nov [3]", "cites": [3]}, 100, ""
            return {"reply": "It moved to 14 November.", "cites": [3], "draft": ""}, 100, ""
        vigil.call_json = fake_call
        self.ask()
        musts = [m for m, _ in prompts]
        self.assertGreater(musts.count("notes"), 1)
        self.assertEqual(musts[-1], "reply")
        self.assertIn("exam moved to 14 Nov [3]", prompts[-1][1])
        self.assertTrue(all(len(c) < 12_000 for _, c in prompts))       # each call fits the free tier
        self.assertIn("#all/t3", self.last()["text"])

    def test_a_rate_limit_is_waited_out(self):
        calls, slept = [], []
        orig_sleep = vc.time.sleep
        vc.time.sleep = slept.append
        vigil.call_json = lambda *a, **k: (calls.append(1) and None or
                                           ((None, 0, "qwen is rate limited") if len(calls) < 2 else
                                            ({"reply": "ok", "cites": []}, 10, "")))
        try:
            self.ask()
        finally:
            vc.time.sleep = orig_sleep
        self.assertEqual(slept, [12])
        self.assertEqual(self.last()["text"], "ok")

    def test_the_chat_hands_mail_questions_to_the_mailbox(self):
        started = []
        orig = vc._read_mail
        vc._read_mail = lambda *a: started.append(a)
        try:
            self.replies.append({"reply": "Let me look 👀", "ops": [
                {"op": "mail", "query": "from:idf", "question": "when is my exam?"}]})
            vc._converse(vc.add_message("me", "when is my exam according to the army?"), None)
            deadline = time.time() + 2
            while not started and time.time() < deadline:
                time.sleep(0.02)
        finally:
            vc._read_mail = orig
        self.assertEqual(started[0][0], "from:idf")
        self.assertEqual(started[0][3]["text"], "when is my exam according to the army?")
        self.assertFalse(vc._live_question("what is the latest email from the army?"))
        self.assertFalse(vc._live_question("מה המייל האחרון מהצבא היום?"))

    def test_follow_ups_see_the_last_search(self):
        self.replies.append({"reply": "ok", "cites": []})
        self.ask()
        ctx = vc._context(vc.add_message("me", "and the one before?"), None)
        self.assertIn("YOUR LAST MAIL SEARCH (from:idf exam) FOUND:", ctx)
        self.assertIn("IDF · Exam [1]", ctx)

    def test_reconnect_button(self):
        self.found = self.gr.MailboxError(self.gr.NOT_CONNECTED)
        self.ask()
        card = self.last()
        self.gr.reconnect = lambda: (True, "")
        w = self.watch()
        vc.api_button(card["id"], "reconnect_gmail")
        deadline = time.time() + 3
        while time.time() < deadline and vc.get_message(card["id"])["meta"].get("resolved") != "Connected ✓":
            time.sleep(0.02)
        self.assertEqual(vc.get_message(card["id"])["meta"]["resolved"], "Connected ✓")
        deadline = time.time() + 2
        while not self.checks and time.time() < deadline:
            time.sleep(0.02)
        self.assertEqual(self.checks, [w["id"]])                        # catches up at once

    def test_reconnect_that_fails_can_be_tried_again(self):
        self.found = self.gr.MailboxError(self.gr.NOT_CONNECTED)
        self.ask()
        card = self.last()
        self.gr.reconnect = lambda: (False, "the sign-in didn't finish")
        vc.api_button(card["id"], "reconnect_gmail")
        deadline = time.time() + 3
        while time.time() < deadline and "didn't connect" not in self.last()["text"]:
            time.sleep(0.02)
        self.assertIn("Gmail didn't connect — the sign-in didn't finish", self.last()["text"])
        self.assertEqual(vc.get_message(card["id"])["meta"]["resolved"], "")      # button back

    def test_a_watch_that_lost_gmail_gets_the_button(self):
        vc.deliver({"id": "n1", "order_id": None, "title": "Army mail", "urgency": "normal",
                    "message": "I can't check “Army mail” right now — Gmail isn't connected.",
                    "trouble": "Gmail isn't connected"})
        self.assertEqual(self.last()["meta"]["buttons"][0]["id"], "reconnect_gmail")


class TestReactionsAndActivity(ChatCase):

    def test_a_reaction_teaches_its_watch(self):
        w = self.watch()
        m = vc.add_message("gil", "Python 3.14.1 is out", order_id=w["id"], meta={"watch": "Army mail"})
        out = vc.api_react(m["id"], "👎")
        self.assertEqual(out["meta"]["reaction"], "👎")
        self.assertEqual(vigil.get(w["id"])["feedback"][0]["verdict"], "down")
        out = vc.api_react(m["id"], "👎")                                 # again: taken back
        self.assertIsNone(out["meta"]["reaction"])
        self.assertEqual(vigil.get(w["id"])["feedback"], [])
        self.assertIsNone(vc.api_react(m["id"], "💩"))                    # not one of ours
        mine = vc.add_message("me", "hi")
        self.assertIsNone(vc.api_react(mine["id"], "👍"))                 # only GIL's messages

    def test_activity_in_plain_words(self):
        w = self.watch()
        st = json.loads(vigil._PATH.read_text(encoding="utf-8"))
        st["orders"][0]["history"] = [{"at": 100, "outcome": "unchanged", "tokens": 0},
                                      {"at": 200, "outcome": "error", "note": "Gmail isn't connected"}]
        vigil._PATH.write_text(json.dumps(st), encoding="utf-8")
        rows = vc._state_extras()["activity"]
        self.assertEqual([r["text"] for r in rows], ["Couldn't check", "Nothing new"])
        self.assertEqual(rows[0]["note"], "Gmail isn't connected")

    def test_his_notes_are_visible_and_clearable(self):
        w = self.watch()
        vigil.update(w["id"], scratch="Waiting on the exam date")
        self.assertEqual(vc._watch_rows()[0]["notes"], "Waiting on the exam date")
        vc.api_watch(w["id"], "clear_notes")
        self.assertEqual(vc._watch_rows()[0]["notes"], "")


if __name__ == "__main__":
    unittest.main()
