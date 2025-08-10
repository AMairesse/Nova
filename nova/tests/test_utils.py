# nova/tests/test_utils.py 
from django.test import SimpleTestCase
from unittest.mock import patch
import importlib
import sys
import types

from nova import utils as utils_mod


class UtilsTests(SimpleTestCase):
    def test_normalize_url_basic_cases(self):
        cases = [
            ("HTTP://Example.COM:80", "http://example.com/"),
            ("https://example.com:443", "https://example.com/"),
            ("http://example.com:8080", "http://example.com:8080/"),
            ("http://example.com/a", "http://example.com/a"),
            ("http://example.com/a;p?q=1#f", "http://example.com/a;p?q=1#f"),
            ("https://example.com:443/path?x=1#frag", "https://example.com/path?x=1#frag"),
        ]
        for input_url, expected in cases:
            with self.subTest(input_url=input_url):
                self.assertEqual(utils_mod.normalize_url(input_url), expected)

    def test_normalize_url_accepts_str_like_object(self):
        class Urlish:
            def __str__(self):
                return "https://EXAMPLE.com"
        self.assertEqual(utils_mod.normalize_url(Urlish()), "https://example.com/")

    def test_normalize_url_preserves_case_in_path_and_lowercases_scheme(self):
        self.assertEqual(
            utils_mod.normalize_url("HTTP://example.com/AbC/Def"),
            "http://example.com/AbC/Def",
        )

    def _fake_langchain_patch(self):
        lc_pkg = types.ModuleType("langchain_core")
        messages_mod = types.ModuleType("langchain_core.messages")

        class BaseMessage:
            def __init__(self, content):
                self.content = content

        class AIMessage(BaseMessage):
            pass

        messages_mod.BaseMessage = BaseMessage
        messages_mod.AIMessage = AIMessage

        return patch.dict(
            sys.modules,
            {
                "langchain_core": lc_pkg,
                "langchain_core.messages": messages_mod,
            },
            clear=False,
        )

    def test_extract_final_answer_with_str(self):
        self.assertEqual(utils_mod.extract_final_answer("hello"), "hello")

    def test_extract_final_answer_with_list_of_messages(self):
        with self._fake_langchain_patch():
            importlib.reload(sys.modules[utils_mod.__name__])
            reloaded_utils = sys.modules[utils_mod.__name__]

            BaseMessage = sys.modules["langchain_core.messages"].BaseMessage
            AIMessage = sys.modules["langchain_core.messages"].AIMessage

            messages = [
                BaseMessage("first"),
                "not a message",
                AIMessage("final answer"),
            ]
            self.assertEqual(reloaded_utils.extract_final_answer(messages), "final answer")

    def test_extract_final_answer_with_list_without_messages(self):
        with self._fake_langchain_patch():
            importlib.reload(sys.modules[utils_mod.__name__])
            reloaded_utils = sys.modules[utils_mod.__name__]

            data = ["a", 1, {"x": "y"}]
            self.assertEqual(reloaded_utils.extract_final_answer(data), str(data))

    def test_extract_final_answer_with_dict_messages(self):
        with self._fake_langchain_patch():
            importlib.reload(sys.modules[utils_mod.__name__])
            reloaded_utils = sys.modules[utils_mod.__name__]

            AIMessage = sys.modules["langchain_core.messages"].AIMessage
            payload = {"messages": ["irrelevant", AIMessage("from dict")]}
            self.assertEqual(reloaded_utils.extract_final_answer(payload), "from dict")

    def test_extract_final_answer_with_other_types(self):
        with self._fake_langchain_patch():
            importlib.reload(sys.modules[utils_mod.__name__])
            reloaded_utils = sys.modules[utils_mod.__name__]

            obj = {"key": "value"}
            self.assertEqual(reloaded_utils.extract_final_answer(obj), str(obj))
