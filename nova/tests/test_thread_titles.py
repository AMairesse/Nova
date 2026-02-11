from django.test import SimpleTestCase

from nova.thread_titles import (
    build_default_thread_subject,
    is_default_thread_subject,
    normalize_generated_thread_title,
)


class ThreadTitlesTests(SimpleTestCase):
    def test_build_default_thread_subject(self):
        self.assertEqual(build_default_thread_subject(1), "New thread 1")
        self.assertEqual(build_default_thread_subject(0), "New thread 1")

    def test_is_default_thread_subject_supports_legacy_and_current_patterns(self):
        self.assertTrue(is_default_thread_subject("thread n°12"))
        self.assertTrue(is_default_thread_subject("New thread 4"))
        self.assertFalse(is_default_thread_subject("Custom subject"))

    def test_normalize_generated_thread_title_cleans_and_truncates(self):
        self.assertEqual(normalize_generated_thread_title('  "Trip to Paris"  '), "Trip to Paris")
        self.assertEqual(normalize_generated_thread_title(""), "")
        self.assertEqual(normalize_generated_thread_title("New thread 7"), "")

        long_title = "A" * 150
        normalized = normalize_generated_thread_title(long_title, max_length=20)
        self.assertEqual(len(normalized), 20)
