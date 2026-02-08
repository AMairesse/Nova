from django.test import SimpleTestCase

from nova.tasks.task_definition_runner import build_email_prompt_variables, render_prompt_template


class TaskDefinitionRunnerUtilsTests(SimpleTestCase):
    def test_render_prompt_template_replaces_known_variables(self):
        template = "Count={{ new_email_count }}\nList={{ new_emails_markdown }}"
        rendered = render_prompt_template(
            template,
            variables={"new_email_count": 2, "new_emails_markdown": "- a\n- b"},
        )

        self.assertIn("Count=2", rendered)
        self.assertIn("List=- a", rendered)

    def test_render_prompt_template_missing_variable_becomes_empty_string(self):
        rendered = render_prompt_template("Hello {{ missing_key }}!", variables={})
        self.assertEqual(rendered, "Hello !")

    def test_build_email_prompt_variables(self):
        headers = [
            {"uid": 10, "from": "alice@example.com", "subject": "A", "date": "2026-02-08T10:00:00+00:00"},
            {"uid": 11, "from": "bob@example.com", "subject": "B", "date": "2026-02-08T10:01:00+00:00"},
        ]
        vars_ = build_email_prompt_variables(headers)
        self.assertEqual(vars_["new_email_count"], 2)
        self.assertEqual(len(vars_["new_emails_json"]), 2)
        self.assertIn("uid=10", vars_["new_emails_markdown"])
