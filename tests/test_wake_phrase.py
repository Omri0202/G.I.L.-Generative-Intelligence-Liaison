"""Tests for wake_phrase.py — pure functions, no network or GUI needed."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from wake_phrase import (
    contains_wake_phrase,
    strip_wake_phrase,
    is_addressed,
    edit_distance,
)


class TestEditDistance(unittest.TestCase):

    def test_identical(self):
        self.assertEqual(edit_distance("gil", "gil"), 0)

    def test_one_insertion(self):
        self.assertEqual(edit_distance("gil", "gill"), 1)

    def test_one_substitution(self):
        self.assertEqual(edit_distance("gil", "gal"), 1)

    def test_one_deletion(self):
        self.assertEqual(edit_distance("gail", "gil"), 1)

    def test_completely_different(self):
        self.assertGreater(edit_distance("abc", "xyz"), 1)

    def test_empty_strings(self):
        self.assertEqual(edit_distance("", ""), 0)
        self.assertEqual(edit_distance("abc", ""), 3)


class TestContainsWakePhrase(unittest.TestCase):

    def test_hello_gil(self):
        self.assertTrue(contains_wake_phrase("hello gil"))

    def test_hey_gil(self):
        self.assertTrue(contains_wake_phrase("hey gil"))

    def test_hi_gil(self):
        self.assertTrue(contains_wake_phrase("hi gil"))

    def test_gil_as_first_word(self):
        self.assertTrue(contains_wake_phrase("gil open chrome"))

    def test_common_mishearing_gill(self):
        self.assertTrue(contains_wake_phrase("hello gill"))

    def test_common_mishearing_gail(self):
        self.assertTrue(contains_wake_phrase("hey gail"))

    def test_normal_speech_no_match(self):
        self.assertFalse(contains_wake_phrase("play some music"))
        self.assertFalse(contains_wake_phrase("open youtube"))
        self.assertFalse(contains_wake_phrase("what time is it"))

    def test_gil_in_middle_not_first_word_no_hello(self):
        # "I asked gil" — gil is not first word, no hello variant
        # This should NOT trigger (no wake pattern)
        self.assertFalse(contains_wake_phrase("I asked gil something"))

    def test_case_insensitive(self):
        self.assertTrue(contains_wake_phrase("Hello GIL"))
        self.assertTrue(contains_wake_phrase("HEY GIL"))


class TestStripWakePhrase(unittest.TestCase):

    def test_strip_hello_gil(self):
        self.assertEqual(strip_wake_phrase("hello gil open youtube"), "open youtube")

    def test_strip_hey_gil(self):
        self.assertEqual(strip_wake_phrase("hey gil what time is it"), "what time is it")

    def test_strip_hi_gil(self):
        self.assertEqual(strip_wake_phrase("hi gil play music"), "play music")

    def test_no_command_after(self):
        self.assertEqual(strip_wake_phrase("hello gil"), "")

    def test_gil_first_word(self):
        result = strip_wake_phrase("gil open chrome")
        self.assertEqual(result, "open chrome")

    def test_leaves_multi_word_command(self):
        result = strip_wake_phrase("hello gil set volume to 50")
        self.assertEqual(result, "set volume to 50")


class TestIsAddressed(unittest.TestCase):

    def test_addressed_by_name(self):
        self.assertTrue(is_addressed("gil open chrome"))
        self.assertTrue(is_addressed("hey gil"))

    def test_addressed_mishearing(self):
        self.assertTrue(is_addressed("gill open chrome"))

    def test_not_addressed(self):
        self.assertFalse(is_addressed("open chrome"))
        self.assertFalse(is_addressed("play music please"))
        self.assertFalse(is_addressed("what time is it"))

    def test_gil_mid_sentence(self):
        # "ask gil" — gil appears, so it IS addressed
        self.assertTrue(is_addressed("ask gil to play music"))


if __name__ == "__main__":
    unittest.main()
