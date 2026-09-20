from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase

from nova.continuous.summarization import SummaryReductionError, _pack_items, _split_item, summarize_day
from nova.tasks.conversation_tasks import SUMMARY_SYSTEM_PROMPT, _build_day_summary_prompt_with_context
from nova.utils import estimate_tokens


DAY = "2026-09-20"


def build_prompt(transcript, **kwargs):
    return _build_day_summary_prompt_with_context(DAY, transcript, **kwargs)


def fitting(limit):
    def fits(*parts):
        rendered = parts[0] if len(parts) == 1 else f"{parts[0]}\n{parts[1]}"
        return estimate_tokens(rendered) <= limit

    return fits


class SummaryEngineTests(IsolatedAsyncioTestCase):
    def assert_prompt_budget(self, prompt, context=4096):
        self.assertLessEqual(
            estimate_tokens(SUMMARY_SYSTEM_PROMPT) + estimate_tokens(prompt) + 8,
            context * 70 // 100,
        )

    async def test_high_context_window_uses_one_real_prompt(self):
        calls = []

        async def generate(prompt):
            self.assert_prompt_budget(prompt, 100_000)
            calls.append(prompt)
            return "compact summary"

        result = await summarize_day(
            messages=[SimpleNamespace(id=7, role="user", text="hello" * 14_000)], day_label=DAY,
            current_summary="ignored", previous_summaries=[], delta_mode=False, context_tokens=100_000,
            generate=generate, build_prompt=build_prompt, system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        self.assertEqual(result, "compact summary")
        self.assertEqual(len(calls), 1)
        self.assertGreater(estimate_tokens(calls[0]), 16_000)
        self.assertIn("hello", calls[0])
        self.assertIn("Messages for this day", calls[0])

    async def test_delta_keeps_huge_current_summary_and_long_message_tail(self):
        calls = []
        current = "CURRENT-" + ("context " * 4500)
        message = "HEAD\n\n" + ("body " * 4200) + "\nTAIL-EXACT"

        async def generate(prompt):
            self.assert_prompt_budget(prompt)
            calls.append(prompt)
            return "compact note"

        await summarize_day(
            messages=[{"id": 1, "role": "user", "text": message}], day_label=DAY,
            current_summary=current, previous_summaries=[], delta_mode=True, context_tokens=4096,
            generate=generate, build_prompt=build_prompt, system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        joined = "\n".join(calls)
        self.assertIn("CURRENT-", joined)
        self.assertIn("TAIL-EXACT", joined)
        self.assertEqual(joined.count("HEAD"), 1)

    async def test_previous_dated_summary_is_context_only_and_not_repeated_in_notes(self):
        calls = []
        previous = [("2026-09-19", "PRIOR-DATED-" + ("old fact " * 1600))]
        messages = [{"id": i, "role": "user" if i % 2 else "assistant", "text": f"NEW-{i} " + ("x " * 300)} for i in range(18)]

        async def generate(prompt):
            self.assert_prompt_budget(prompt)
            calls.append(prompt)
            return "compact note"

        await summarize_day(
            messages=messages, day_label=DAY, current_summary="", previous_summaries=previous,
            delta_mode=False, context_tokens=4096, generate=generate, build_prompt=build_prompt,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        self.assertGreater(len(calls), 3)
        main_calls = [call for call in calls if "NEW-" in call]
        self.assertTrue(main_calls)
        self.assertTrue(all("PRIOR-DATED-" not in call for call in main_calls))
        self.assertTrue(any("PRIOR-DATED-" in call for call in calls))
        self.assertTrue(any("2026-09-19" in call for call in calls))

    async def test_many_short_messages_are_all_present(self):
        calls = []

        async def generate(prompt):
            self.assert_prompt_budget(prompt)
            calls.append(prompt)
            return "compact note"

        messages = [{"id": i, "role": "user", "text": f"short-{i:04d}!"} for i in range(800)]
        await summarize_day(
            messages=messages, day_label=DAY, current_summary="", previous_summaries=[], delta_mode=False,
            context_tokens=4096, generate=generate, build_prompt=build_prompt, system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        joined = "\n".join(calls)
        self.assertGreater(len(calls), 1)
        for i in range(800):
            self.assertEqual(joined.count(f"short-{i:04d}!"), 1)

    async def test_consolidates_an_additional_level_only_when_notes_exceed_budget(self):
        calls = []

        async def generate(prompt):
            self.assert_prompt_budget(prompt)
            calls.append(prompt)
            return "SYNTHETIC NOTE " * 80

        await summarize_day(
            messages=[{"id": i, "role": "user", "text": "evidence " * 450} for i in range(35)],
            day_label=DAY, current_summary="", previous_summaries=[], delta_mode=False,
            context_tokens=4096, generate=generate, build_prompt=build_prompt,
            system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        intermediate = [prompt for prompt in calls if prompt.startswith("Condense")]
        self.assertTrue(any("chronological notes" in prompt for prompt in intermediate))
        self.assertEqual(sum("Return only non-empty Markdown sections" in prompt for prompt in calls), 1)

    def test_split_item_preserves_every_character_and_unicode_without_whitespace(self):
        body = "αβγ漢字🙂" * 37
        parts = _split_item("[message id=1 role=User]", body, fitting(12))
        self.assertGreater(len(parts), 1)
        self.assertEqual("".join(text for _, text in parts), body)
        self.assertTrue(all(label.startswith("[message id=1 role=User]") for label, _ in parts))

    def test_split_item_prefers_paragraphs_and_newlines_but_keeps_exact_body(self):
        body = "first paragraph\n\nsecond line\nthird paragraph\n\nfinal paragraph " * 4
        parts = _split_item("[message id=2 role=User]", body, fitting(16))
        self.assertEqual("".join(text for _, text in parts), body)
        self.assertGreater(len(parts), 2)
        self.assertTrue(any("\n\n" in text for _, text in parts[:-1]))

    def test_pack_items_renders_labels_and_every_chunk_fits(self):
        items = [(f"[message id={i} role=User]", f"payload-{i} " * 12) for i in range(7)]
        fits = fitting(18)
        chunks = _pack_items(items, fits)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(fits(chunk) for chunk in chunks))
        for label, text in items:
            self.assertIn(label, "\n".join(chunks))
            fragment_text = "".join(chunk.split("\n", 1)[1] for chunk in chunks if label in chunk)
            self.assertEqual(fragment_text, text)

    async def test_empty_input_is_explicit_failure(self):
        async def generate(prompt):
            return "unused"

        with self.assertRaises(SummaryReductionError):
            await summarize_day(
                messages=[], day_label=DAY, current_summary="", previous_summaries=[], delta_mode=True,
                context_tokens=4096, generate=generate, build_prompt=build_prompt, system_prompt=SUMMARY_SYSTEM_PROMPT,
            )

    async def test_empty_and_nonshrinking_outputs_fail(self):
        async def empty(prompt):
            return ""

        async def nonshrinking(prompt):
            return "x " * 10_000

        kwargs = dict(
            messages=[{"id": 1, "role": "user", "text": "small source " * 5000}], day_label=DAY,
            current_summary="", previous_summaries=[], delta_mode=False, context_tokens=4096,
            build_prompt=build_prompt, system_prompt=SUMMARY_SYSTEM_PROMPT,
        )
        with self.assertRaises(SummaryReductionError):
            await summarize_day(generate=empty, **kwargs)
        with self.assertRaises(SummaryReductionError):
            await summarize_day(generate=nonshrinking, **kwargs)
