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
        self.assertEqual(json.loads(body)["short_name"], "Vigil")


class _FakeRequest:
    """Just enough of a request for the handler's access rules."""

    def __init__(self, ip, headers=None, path="/api"):
        self.client_address = (ip, 5000)
        self.headers = headers or {}
        self.path = path

    def _local(self):
        return vc._Handler._local(self)


class TestPhone(ChatCase):

    def setUp(self):
        super().setUp()
        self._orig_phone = (vc._rebind, vc._port)
        self.binds = []
        vc._rebind = self.binds.append
        vc._port = 8741
        import mobile
        self._orig_ip = mobile.lan_ip
        mobile.lan_ip = lambda: "192.168.1.20"

    def tearDown(self):
        import mobile
        mobile.lan_ip = self._orig_ip
        vc._rebind, vc._port = self._orig_phone
        super().tearDown()

    def ok(self, ip, headers=None, path="/api"):
        return vc._Handler._token_ok(_FakeRequest(ip, headers, path))

    def test_off_by_default_and_the_network_is_shut_out(self):
        self.assertFalse(vc._phone()["on"])
        self.assertTrue(self.ok("127.0.0.1"))
        self.assertFalse(self.ok("192.168.1.30", {"X-GIL-Token": "anything"}))

    def test_pairing_gives_a_link_and_a_qr_code(self):
        view = vc.api_phone(True)
        token = vc._phone()["token"]
        self.assertEqual(self.binds, ["0.0.0.0"])
        self.assertEqual(view["url"], f"http://192.168.1.20:8741/?t={token}")
        self.assertTrue(view["qr"].startswith("<svg") or view["qr"] == "")
        self.assertGreaterEqual(len(token), 20)
        # the state every window (and the phone) receives never carries it
        self.assertNotIn(token, json.dumps(vc._state_extras()))
        self.assertTrue(vc._state_extras()["phone"]["on"])

    def test_the_phone_needs_the_token(self):
        vc.api_phone(True)
        token = vc._phone()["token"]
        self.assertTrue(self.ok("192.168.1.30", {"X-GIL-Token": token}))
        self.assertTrue(self.ok("192.168.1.30", {}, f"/events?t={token}"))
        self.assertFalse(self.ok("192.168.1.30", {"X-GIL-Token": token[:-1] + "x"}))
        self.assertFalse(self.ok("192.168.1.30", {}))

    def test_turning_it_off_kills_the_old_link(self):
        vc.api_phone(True)
        token = vc._phone()["token"]
        vc.api_phone(False)
        self.assertEqual(self.binds, ["0.0.0.0", "127.0.0.1"])
        self.assertFalse(self.ok("192.168.1.30", {"X-GIL-Token": token}))
        vc.api_phone(True)
        self.assertNotEqual(vc._phone()["token"], token)

    def test_a_phone_cant_open_phone_access_or_windows(self):
        self.assertEqual(vc._LOCAL_ONLY, {"phone", "open"})


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


if __name__ == "__main__":
    unittest.main()
