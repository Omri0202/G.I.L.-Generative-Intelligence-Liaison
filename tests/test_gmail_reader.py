"""Tests for gmail_reader.py — a fake Gmail service, so nothing touches the
user's real mailbox."""
import sys, os, base64, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
import gmail_recap
import gmail_reader as gr


def b64(text):
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def message(mid, frm="IDF <info@idf.il>", subject="Your exam", body="Exam on 12 Nov.",
            html=False, at=None, labels=None, thread=None):
    part = {"mimeType": "text/html" if html else "text/plain", "body": {"data": b64(body)}}
    return {"id": mid, "threadId": thread or "t" + mid, "labelIds": labels or ["INBOX"],
            "internalDate": str(int((at or time.time()) * 1000)), "snippet": body[:40],
            "payload": {"mimeType": "multipart/alternative", "parts": [part],
                        "headers": [{"name": "From", "value": frm},
                                    {"name": "To", "value": "Omri <me@example.com>"},
                                    {"name": "Subject", "value": subject}]}}


class _Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeGmail:
    """messages().list/get, threads().list/get, getProfile and batches."""

    def __init__(self, messages=(), threads=(), pages=None):
        self.messages_by_id = {m["id"]: m for m in messages}
        self.threads_by_id = {t["id"]: t for t in threads}
        self.pages = pages
        self.queries = []

    def users(self):
        return self

    def getProfile(self, userId):
        return _Exec({"emailAddress": "me@example.com"})

    def messages(self):
        fake = self

        class M:
            def list(self, userId, q=None, maxResults=100, pageToken=None, labelIds=None):
                fake.queries.append(q)
                if fake.pages is not None:
                    return _Exec(fake.pages[int(pageToken or 0)])
                return _Exec({"messages": [{"id": i} for i in fake.messages_by_id][:maxResults],
                              "resultSizeEstimate": len(fake.messages_by_id)})

            def get(self, userId, id, format, metadataHeaders=None):
                return ("m", id)
        return M()

    def threads(self):
        fake = self

        class T:
            def list(self, userId, q=None, maxResults=100):
                return _Exec({"threads": [{"id": i} for i in fake.threads_by_id]})

            def get(self, userId, id, format, metadataHeaders=None):
                return ("t", id)
        return T()

    def new_batch_http_request(self, callback):
        fake = self

        class Batch:
            def __init__(self):
                self.items = []

            def add(self, req, request_id):
                self.items.append((req, request_id))

            def execute(self):
                for (kind, item_id), rid in self.items:
                    store = fake.messages_by_id if kind == "m" else fake.threads_by_id
                    callback(rid, store.get(item_id), None)
        return Batch()


class ReaderCase(unittest.TestCase):

    def setUp(self):
        self._orig = gmail_recap._get_service
        gr._me.clear()

    def tearDown(self):
        gmail_recap._get_service = self._orig
        gr._me.clear()

    def use(self, fake):
        gmail_recap._get_service = lambda: fake
        return fake


class TestReading(ReaderCase):

    def test_not_connected_is_loud(self):
        gmail_recap._get_service = lambda: None
        with self.assertRaises(gr.MailboxError) as ctx:
            gr.search("from:idf")
        self.assertIn("isn't connected", str(ctx.exception))
        with self.assertRaises(gr.MailboxError):
            gr.unread()

    def test_a_dead_login_mid_call_is_loud_too(self):
        class Denied(Exception):
            resp = type("R", (), {"status": 401})()
        fake = self.use(FakeGmail())
        fake.pages = None
        fake.messages = lambda: type("M", (), {"list": lambda *a, **k: _Exec(Denied())})()
        with self.assertRaises(gr.MailboxError) as ctx:
            gr.search("x")
        self.assertIn("isn't connected", str(ctx.exception))

    def test_search_reads_bodies_newest_first(self):
        self.use(FakeGmail([message("1", body="Exam on 12 Nov."), message("2", body="Bus at 7")]))
        mails, total = gr.search("from:idf", limit=40)
        self.assertEqual([m["id"] for m in mails], ["1", "2"])
        self.assertEqual(total, 2)
        self.assertEqual(mails[0]["body"], "Exam on 12 Nov.")
        self.assertEqual(mails[0]["from"], "IDF")
        self.assertEqual(mails[0]["link"], "https://mail.google.com/mail/u/0/#all/t1")

    def test_search_pages_and_reports_how_many_more(self):
        msgs = [message(str(i)) for i in range(5)]
        fake = self.use(FakeGmail(msgs, pages=[
            {"messages": [{"id": "0"}, {"id": "1"}], "nextPageToken": "1", "resultSizeEstimate": 120},
            {"messages": [{"id": "2"}, {"id": "3"}], "nextPageToken": "2"}]))
        mails, total = gr.search("older_than:1y", limit=3)
        self.assertEqual(len(mails), 3)
        self.assertEqual(total, 120)
        self.assertEqual(fake.queries, ["older_than:1y", "older_than:1y"])

    def test_html_mail_becomes_text(self):
        html = "<html><head><style>p{}</style></head><body><p>Hello</p><p>Exam <b>moved</b></p><script>x()</script></body></html>"
        self.use(FakeGmail([message("1", body=html, html=True)]))
        body = gr.search("x")[0][0]["body"]
        self.assertIn("Hello", body)
        self.assertIn("Exam moved", body)
        self.assertNotIn("x()", body)
        self.assertNotIn("p{}", body)

    def test_quoted_history_is_cut(self):
        self.assertEqual(gr.clean("Sounds good!\n\nOn Mon, 3 Mar 2025, Dana <d@x.com> wrote:\n> old stuff"),
                         "Sounds good!")
        self.assertEqual(gr.clean("מעולה, תודה\n\nבתאריך יום ב׳, 3 במרץ 2025 ב-10:00 מאת דנה <d@x.com>:\n> ישן"),
                         "מעולה, תודה")
        self.assertEqual(gr.clean("a​‏b\n\n\n\nc"), "ab\n\nc")

    def test_long_bodies_are_clipped(self):
        self.use(FakeGmail([message("1", body="word " * 2000)]))
        body = gr.search("x", body_chars=300)[0][0]["body"]
        self.assertLessEqual(len(body), 301)
        self.assertTrue(body.endswith("…"))


class TestUnanswered(ReaderCase):

    def thread(self, tid, *msgs):
        return {"id": tid, "messages": list(msgs)}

    def test_only_people_waiting_on_you(self):
        old = time.time() - 3 * 86400
        self.use(FakeGmail(threads=[
            self.thread("a", message("a1", frm="Dana <dana@x.com>", subject="Can you send it?", at=old)),
            self.thread("b", message("b1", frm="Dana <dana@x.com>", at=old),
                        message("b2", frm="Me <me@example.com>", at=old + 60, labels=["SENT"])),
            self.thread("c", message("c1", frm="Shop <noreply@shop.com>", at=old)),
            self.thread("d", message("d1", frm="Yossi <y@x.com>", at=time.time() - 600)),
        ]))
        waiting = gr.unanswered()
        self.assertEqual([w["subject"] for w in waiting], ["Can you send it?"])
        self.assertEqual(waiting[0]["days"], 3)


if __name__ == "__main__":
    unittest.main()
