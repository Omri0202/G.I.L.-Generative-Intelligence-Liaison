"""Tests for checkout.py — the hard lines, then a whole run through a fake
shop in a real (headless) browser with a scripted decider: the user's saved
details go in, the card field and the pay button are never touched."""
import sys, os, json, shutil, threading, functools
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import importlib.util
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
import checkout as co

DETAILS = {"name": "Omri Levi", "email": "omri@example.com", "phone": "0501234567"}


def el(n=1, tag="input", type="text", text="", attrs="", filled=False):
    return {"n": n, "tag": tag, "type": type, "text": text, "attrs": attrs, "filled": filled,
            "role": "", "href": "", "checked": False, "inView": True, "options": [], "ctx": ""}


class TestHardLines(unittest.TestCase):

    def test_pay_and_place_order_are_never_clicked(self):
        for text in ("Pay ₪9.09", "Place order", "Complete purchase", "Confirm and pay", "שלם עכשיו",
                     "בצע הזמנה", "אישור הזמנה", "Submit payment"):
            ok, why, _ = co.allowed({"action": "click"}, el(tag="button", type="submit", text=text), DETAILS)
            self.assertFalse(ok, text)
            self.assertIn("user's", why)
        for text in ("Buy now", "Add to cart", "Checkout ₪9.09", "Continue to payment", "המשך לתשלום",
                     "הוספה לעגלה", "Reject all"):
            self.assertTrue(co.allowed({"action": "click"}, el(tag="button", text=text), DETAILS)[0], text)

    def test_verification_codes_are_the_users(self):
        for e in (el(tag="button", text="Send code"), el(tag="button", text="שלח קוד"),
                  el(tag="button", text="Continue", attrs="phone-otp-submit")):
            self.assertFalse(co.allowed({"action": "click"}, e, DETAILS)[0])

    def test_card_and_password_fields_are_never_typed_into(self):
        for attrs in ("cc-number cardnumber", "cvc", "exp-date", "password current-password",
                      "מספר כרטיס", "תעודת זהות"):
            self.assertEqual(co.field_kind(el(attrs=attrs)), "", attrs)
            ok, _, _ = co.allowed({"action": "fill", "field": "email"}, el(attrs=attrs), DETAILS)
            self.assertFalse(ok, attrs)
        # a plan picked by a radio whose class says "card" is still fine to click
        self.assertTrue(co.allowed({"action": "click"}, el(type="radio", attrs="plan-card"), DETAILS)[0])

    def test_only_the_saved_details_go_in(self):
        ok, _, value = co.allowed({"action": "fill", "field": "email"},
                                  el(type="email", attrs="email"), DETAILS)
        self.assertEqual((ok, value), (True, "omri@example.com"))
        ok, _, value = co.allowed({"action": "fill", "field": "first_name"},
                                  el(attrs="first_name given-name"), DETAILS)
        self.assertEqual(value, "Omri")
        # the model names the field; it never supplies what gets typed
        ok, why, _ = co.allowed({"action": "fill", "field": "email", "value": "evil@x.com"},
                                el(attrs="coupon code"), DETAILS)
        self.assertFalse(ok)
        self.assertFalse(co.allowed({"action": "fill", "field": "phone"}, el(type="tel"), {})[0])

    def test_where_it_is(self):
        self.assertEqual(co.page_state({"fields": "text cc-number Card number", "iframes": "", "url": ""}), "payment")
        self.assertEqual(co.page_state({"fields": "", "iframes": "https://js.stripe.com/v3/elements", "url": ""}), "payment")
        pay_btn = [el(tag="button", text="Pay ₪9.09")]
        self.assertEqual(co.page_state({"fields": "", "iframes": "", "url": "https://shop.example/checkout",
                                        "elements": pay_btn}), "payment")
        # a "Buy with Shop Pay" button on a product page is not the payment step
        self.assertEqual(co.page_state({"fields": "", "iframes": "", "url": "https://shop.example/esim/poland",
                                        "elements": [el(tag="button", text="Buy with Shop Pay")]}), "")
        self.assertEqual(co.page_state({"fields": "email password", "iframes": "", "url": "", "elements": []}), "signin")
        self.assertEqual(co.page_state({"fields": "", "iframes": "https://www.google.com/recaptcha/api2/anchor?size=normal",
                                        "url": "", "elements": []}), "captcha")
        self.assertEqual(co.page_state({"fields": "", "iframes": "https://www.google.com/recaptcha/api2/anchor?size=invisible",
                                        "url": "", "elements": []}), "")

    def test_stays_on_the_shop(self):
        self.assertTrue(co.same_shop("https://checkout.andalu.com/x", "https://andalu.com/esim"))
        self.assertTrue(co.same_shop("https://shop.co.il/cart", "https://www.shop.co.il/p"))
        self.assertTrue(co.same_shop("https://checkout.shopify.com/123", "https://store.example/p"))
        self.assertFalse(co.same_shop("https://evil.example/", "https://andalu.com/esim"))
        self.assertFalse(co.same_shop("https://other.co.il/", "https://shop.co.il/"))

    def test_the_page_as_the_model_sees_it(self):
        obs = {"url": "u", "title": "t", "text": "x", "elements": [
            dict(el(0, "button", "button", "הוספה לעגלה"), ctx="5GB ל־30 ימים ₪18"),
            dict(el(1, "button", "button", "5G Play"), ctx="5GB ל־30 ימים ₪18"),
            dict(el(2, "button", "button", "הוספה לעגלה"), ctx="10GB ל־30 ימים ₪29")]}
        text = co._describe(obs)
        self.assertEqual(text.count("in “5GB ל־30 ימים ₪18”"), 1)       # once per card
        self.assertIn("in “10GB ל־30 ימים ₪29”", text)


SHOP = {
    "index.html": """<html><head><title>Fake eSIM shop</title></head><body>
      <div class="cookie"><button onclick="this.parentElement.remove()">Reject all</button></div>
      <div class="plan"><b>Poland 3GB · 7 days · ₪12</b><button onclick="location='cart.html?p=3'">Add to cart</button></div>
      <div class="plan"><b>Poland 5GB · 30 days · ₪18</b><button onclick="location='cart.html?p=5'">Add to cart</button></div>
      </body></html>""",
    "cart.html": """<html><head><title>Cart</title></head><body>
      <p id="item"></p><script>document.getElementById('item').textContent = 'In cart: Poland ' + new URLSearchParams(location.search).get('p') + 'GB';</script>
      <a href="checkout.html">Checkout</a></body></html>""",
    "checkout.html": """<html><head><title>Checkout</title></head><body>
      <label>Email <input type="email" name="email" autocomplete="email"></label>
      <label>Full name <input name="full_name" autocomplete="name"></label>
      <label>Card number <input name="cardnumber" autocomplete="cc-number"></label>
      <label>Coupon <input name="coupon"></label>
      <button id="pay" onclick="window.PAID = true">Pay ₪18</button></body></html>""",
}


def _have_chromium() -> bool:
    """Playwright installed and its Chromium downloaded: what a whole run needs."""
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            pw.chromium.launch(headless=True).close()
        return True
    except Exception:
        return False


class TestNoBrowser(unittest.TestCase):
    """Without Playwright (or its Chromium) the order never starts, and the
    user is told what to run instead of "something went wrong"."""

    OFFER = {"seller": "Fake", "name": "5GB", "price": "₪18", "url": "https://shop.example/plan"}

    def test_playwright_not_installed(self):
        with mock.patch.dict(sys.modules, {"playwright": None, "playwright.sync_api": None}):
            res = co.run(self.OFFER, DETAILS, headless=True)
        self.assertFalse(res["ok"])
        self.assertEqual(res["state"], "no_browser")
        self.assertIn("Playwright isn't installed", res["why"])
        self.assertEqual(res["url"], self.OFFER["url"])
        self.assertEqual(co._held, [])                                         # nothing opened

    def test_chromium_not_downloaded(self):
        stopped = []

        class FakePW:
            class chromium:
                @staticmethod
                def launch(**_kw):
                    raise RuntimeError("Executable doesn't exist at C:\\ms-playwright\\chrome.exe\n"
                                       "Please run the following command: playwright install")

            def stop(self):
                stopped.append(True)

        sync_api = mock.MagicMock()
        sync_api.sync_playwright.return_value.start.return_value = FakePW()
        with mock.patch.dict(sys.modules, {"playwright": mock.MagicMock(),
                                           "playwright.sync_api": sync_api}):
            res = co.run(self.OFFER, DETAILS, headless=True)
        self.assertEqual(res["state"], "no_browser")
        self.assertIn("Chromium", res["why"])
        self.assertEqual(stopped, [True])                                      # driver not left running

    def test_the_message_says_what_to_run(self):
        res = {"ok": False, "state": "no_browser", "why": "Playwright isn't installed",
               "url": self.OFFER["url"], "trace": []}
        for he in (False, True):
            text = co.summary(res, self.OFFER, he)
            self.assertIn("python -m playwright install chromium", text)
            self.assertIn(self.OFFER["url"], text)
            self.assertNotIn("open on your PC", text)

    def test_the_packaged_app_says_to_run_from_source(self):
        res = {"ok": False, "state": "no_browser", "why": "Playwright isn't installed",
               "url": self.OFFER["url"], "trace": []}
        with mock.patch.object(sys, "frozen", True, create=True):
            text = co.summary(res, self.OFFER)
        self.assertIn("gil.pyw", text)
        self.assertNotIn("pip install", text)


class TestAWholeRun(unittest.TestCase):
    """A fake shop, a real headless browser, and a decider that does what a
    model would — including trying to press Pay and to type into the card."""

    @classmethod
    def setUpClass(cls):
        if not _have_chromium():
            raise unittest.SkipTest("needs Playwright and its Chromium "
                                    "(python -m playwright install chromium)")
        import tempfile
        cls.dir = tempfile.mkdtemp()
        for name, html in SHOP.items():
            with open(os.path.join(cls.dir, name), "w", encoding="utf-8") as f:
                f.write(html)
        handler = functools.partial(SimpleHTTPRequestHandler, directory=cls.dir)
        handler.log_message = lambda *a: None
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}/"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self._orig = co._decide
        co._held.clear()

    def tearDown(self):
        co._decide = self._orig
        for pw, browser in co._held:
            try:
                browser.close()
                pw.stop()
            except Exception:
                pass
        co._held.clear()

    def script(self, *moves):
        """Each move picks an element by its words, as a model would."""
        moves = list(moves)

        def decide(prompt, obs, history):
            if not moves:
                return {"action": "done"}
            kind, words = moves.pop(0)
            fits = lambda e: kind != "click" or e["tag"] in ("button", "a")
            target = (next((e for e in obs["elements"] if words in e["text"] and fits(e)), None)
                      or next((e for e in obs["elements"] if words in e.get("ctx", "") and fits(e)
                               and e["text"] == "Add to cart"), None))
            return {"action": kind, "n": target["n"] if target else -1,
                    "field": "email" if kind == "fill" else None}
        co._decide = decide

    def page(self):
        return co._held[-1][1].contexts[0].pages[-1]

    def test_to_the_payment_page_with_the_details_in_and_nothing_paid(self):
        self.script(("click", "Reject all"), ("click", "5GB"), ("click", "Checkout"),
                    ("click", "Pay"), ("fill", "Card number"))
        res = co.run({"seller": "Fake", "name": "5GB 30 days", "price": "₪18", "url": self.base + "index.html"},
                     DETAILS, headless=True)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["state"], "payment")
        page = self.page()
        self.assertEqual(page.input_value("input[name=email]"), "omri@example.com")
        self.assertEqual(page.input_value("input[name=full_name]"), "Omri Levi")
        self.assertEqual(page.input_value("input[name=cardnumber]"), "")       # never the card
        self.assertEqual(page.input_value("input[name=coupon]"), "")
        self.assertIsNone(page.evaluate("window.PAID"))                         # never paid
        self.assertIn("filled the user's saved details", res["trace"])

    def test_a_refused_move_is_written_down_and_the_run_goes_on(self):
        # on the product page the "model" asks to type the email into a button: refused
        self.script(("fill", "Reject all"), ("click", "5GB"), ("click", "Checkout"))
        res = co.run({"seller": "Fake", "name": "5GB", "price": "₪18", "url": self.base + "index.html"},
                     {}, headless=True)
        self.assertTrue(res["ok"], res)
        self.assertTrue(any(t.startswith("refused: fill") for t in res["trace"]))
        self.assertEqual(self.page().input_value("input[name=email]"), "")     # nothing saved: nothing typed

    def test_one_order_at_a_time(self):
        co._busy.acquire()
        try:
            res = co.run({"url": self.base + "index.html"}, {}, headless=True)
        finally:
            co._busy.release()
        self.assertEqual(res["state"], "busy")


if __name__ == "__main__":
    unittest.main()
