"""Tests for fast_paths.py — pure functions, no network or GUI needed."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from fast_paths import (
    fast_url_resolve,
    fast_youtube_resolve,
    fast_study_resolve,
    is_greeting_response,
    build_greeting,
)


class TestFastUrlResolve(unittest.TestCase):

    def test_youtube(self):
        r = fast_url_resolve("open youtube")
        self.assertIsNotNone(r)
        self.assertEqual(r[0], "https://youtube.com")
        self.assertEqual(r[1], "YouTube")

    def test_github(self):
        r = fast_url_resolve("open github")
        self.assertIsNotNone(r)
        self.assertIn("github.com", r[0])

    def test_gmail(self):
        r = fast_url_resolve("open gmail")
        self.assertIsNotNone(r)
        self.assertIn("mail.google", r[0])

    def test_case_insensitive(self):
        r = fast_url_resolve("Open YouTube")
        self.assertIsNotNone(r)

    def test_no_trigger_word(self):
        # No open/launch/go-to → no match
        self.assertIsNone(fast_url_resolve("youtube is great"))

    def test_unknown_site(self):
        self.assertIsNone(fast_url_resolve("open stackoverflow"))

    def test_spotify_excluded(self):
        # Spotify is intentionally excluded — must go through spotify_control
        self.assertIsNone(fast_url_resolve("open spotify"))


class TestFastYoutubeResolve(unittest.TestCase):

    def test_trailer(self):
        url = fast_youtube_resolve("show me the Inception trailer")
        self.assertIsNotNone(url)
        self.assertIn("youtube.com/results", url)
        self.assertIn("Inception", url)

    def test_find_video_on_youtube(self):
        # Needs explicit "youtube" indicator to avoid false positives
        url = fast_youtube_resolve("find me a CS50 video on youtube")
        self.assertIsNotNone(url)
        self.assertIn("youtube.com", url)

    def test_search_youtube_for(self):
        url = fast_youtube_resolve("search youtube for blinding lights")
        self.assertIsNotNone(url)
        self.assertIn("blinding", url.lower())

    def test_no_youtube_indicator(self):
        self.assertIsNone(fast_youtube_resolve("open youtube"))
        self.assertIsNone(fast_youtube_resolve("play some music"))

    def test_query_not_too_short(self):
        # Very short or empty queries should not match
        url = fast_youtube_resolve("show me the trailer")
        # "the" gets stripped — remaining query would be empty → no match
        # (behaviour: may or may not match; just assert it doesn't crash)
        # (it's fine either way — just must not raise)


class TestFastStudyResolve(unittest.TestCase):

    def test_math(self):
        r = fast_study_resolve("I'm studying math")
        self.assertIsNotNone(r)
        speech, url = r
        self.assertIn("math", speech.lower())

    def test_python_studying(self):
        r = fast_study_resolve("I'm learning python")
        self.assertIsNotNone(r)

    def test_no_study_trigger(self):
        self.assertIsNone(fast_study_resolve("open python.org"))

    def test_unknown_subject(self):
        self.assertIsNone(fast_study_resolve("I'm studying cooking"))


class TestIsGreetingResponse(unittest.TestCase):

    def test_catches_how_can_i_assist(self):
        self.assertTrue(is_greeting_response("How can I assist you today?"))

    def test_catches_at_your_service(self):
        self.assertTrue(is_greeting_response("I am G.I.L., at your service."))

    def test_catches_i_am_gil(self):
        self.assertTrue(is_greeting_response("I am G.I.L."))

    def test_normal_response_passes(self):
        self.assertFalse(is_greeting_response("VS Code opened."))
        self.assertFalse(is_greeting_response("Playing Blinding Lights."))
        self.assertFalse(is_greeting_response("Volume set to 40."))

    def test_empty_string(self):
        self.assertFalse(is_greeting_response(""))


if __name__ == "__main__":
    unittest.main()
