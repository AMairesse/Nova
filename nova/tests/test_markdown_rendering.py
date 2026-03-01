from django.test import SimpleTestCase

from nova.utils import markdown_to_html


class MarkdownRenderingTests(SimpleTestCase):
    def test_nested_lists_with_two_space_indentation_are_rendered(self):
        content = (
            "- Parent\n"
            "  - Child A\n"
            "    - Child A.1\n"
            "- Parent 2\n"
        )

        rendered = str(markdown_to_html(content))

        self.assertGreaterEqual(rendered.count("<ul>"), 2)
        self.assertIn("Child A.1", rendered)

    def test_markdown_supports_headings_tables_and_soft_line_breaks(self):
        content = (
            "## Summary\n\n"
            "| Name | Score |\n"
            "| --- | --- |\n"
            "| Nova | 10 |\n\n"
            "line one\n"
            "line two\n"
        )

        rendered = str(markdown_to_html(content))

        self.assertIn("<h2>", rendered)
        self.assertIn("<table>", rendered)
        self.assertIn("line one<br>", rendered)

    def test_markdown_sanitization_strips_script_tag(self):
        content = "<script>alert(1)</script>\n\nSafe text"

        rendered = str(markdown_to_html(content))

        self.assertNotIn("<script", rendered.lower())
        self.assertIn("Safe text", rendered)
