from pathlib import Path

from django.test import SimpleTestCase


class ComposerDraftFrontendContractTests(SimpleTestCase):
    script = Path(__file__).parents[1] / "static/js/message-manager-composer.js"

    def test_drafts_are_user_thread_scoped_and_attachments_are_not_serialized(self):
        source = self.script.read_text()
        self.assertIn("nova:composer-draft:v1:", source)
        self.assertIn("attachmentNames", source)
        self.assertIn("sessionStorage.setItem", source)
        self.assertIn("submissionKey", source)
        self.assertNotIn("attachment.file.arrayBuffer", source)

    def test_submission_key_survives_errors_and_dispatch_failure_is_visible(self):
        source = self.script.read_text()
        self.assertIn("getComposerSubmissionKey", source)
        self.assertIn("crypto.getRandomValues", source)
        self.assertIn("formData.set('submission_key', submissionKey)", source)
        self.assertIn("data.dispatch_failed", source)
        self.assertIn("data.task_id && !data.dispatch_failed", source)

    def test_fallback_submission_keys_are_not_constant_and_preserve_new_input_on_error(self):
        source = self.script.read_text()
        self.assertIn("'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'", source)
        self.assertIn("if (!textarea.value) textarea.value = originalMessage", source)
        self.assertIn("this.composerSubmissionKeys.delete(draftKey)", source)
