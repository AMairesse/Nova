from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.test import RequestFactory, TestCase
from django.urls import reverse
from nova.models.Memory import MemoryItem, MemoryItemEmbedding
from nova.models.UserObjects import UserParameters
from user_settings.views.memory import MemorySettingsView


User = get_user_model()


class MemorySettingsViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory-user",
            email="memory@example.com",
            password="pass123",
        )
        self.client.login(username="memory-user", password="pass123")
        self.url = reverse("user_settings:memory")
        self.partial_url = f"{self.url}?partial=1"
        self.parameters, _ = UserParameters.objects.get_or_create(user=self.user)

    def _payload(
        self,
        *,
        enabled=False,
        url="",
        model="",
        api_key="",
        action=None,
        confirm=None,
    ):
        data = {
            "from": "memory",
            "memory_embeddings_url": url,
            "memory_embeddings_model": model,
            "memory_embeddings_api_key": api_key,
        }
        if enabled:
            data["memory_embeddings_enabled"] = "on"
        if action:
            data["action"] = action
        if confirm is not None:
            data["confirm"] = str(confirm)
        return data

    def _create_embedding(self, *, content="remember this"):
        item = MemoryItem.objects.create(
            user=self.user,
            type="fact",
            content=content,
        )
        return MemoryItemEmbedding.objects.create(user=self.user, item=item)

    def _messages(self, response):
        return [message.message for message in get_messages(response.wsgi_request)]

    @patch("user_settings.views.memory.get_embeddings_provider")
    def test_get_partial_prefills_pending_values_and_context(self, mocked_get_provider):
        pending = {
            "memory_embeddings_enabled": True,
            "memory_embeddings_url": "https://pending.example.com/v1",
            "memory_embeddings_model": "embed-pending",
            "memory_embeddings_api_key": "pending-secret",
        }
        session = self.client.session
        session["memory_embeddings_pending"] = pending
        session.save()
        self.parameters.delete()
        self._create_embedding(content="first")
        self._create_embedding(content="second")
        mocked_get_provider.return_value = SimpleNamespace(
            base_url="https://active.example.com/v1",
            model="active-model",
            provider_type="custom_http",
        )

        response = self.client.get(self.partial_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/memory_form.html")
        self.assertEqual(UserParameters.objects.filter(user=self.user).count(), 1)
        self.assertTrue(response.context["has_pending_reembed"])
        self.assertEqual(response.context["pending_reembed_count"], 2)
        self.assertContains(
            response,
            'value="https://pending.example.com/v1"',
            html=False,
        )
        self.assertContains(response, "Active provider")

    def test_get_full_page_uses_full_template(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/memory_form.html")
        self.assertContains(response, "Memory: embeddings settings")
        self.assertContains(response, "Memory browser")

    def test_form_valid_delegates_to_update_view(self):
        request = RequestFactory().post(self.url)
        request.user = self.user
        view = MemorySettingsView()
        view.request = request

        with patch.object(
            SuccessMessageMixin,
            "form_valid",
            return_value=HttpResponse("ok"),
        ) as mocked_form_valid:
            response = view.form_valid(Mock(cleaned_data={}))

        self.assertEqual(response.content, b"ok")
        mocked_form_valid.assert_called_once()

    def test_cancel_reembed_htmx_clears_pending_session(self):
        session = self.client.session
        session["memory_embeddings_pending"] = {"memory_embeddings_url": "https://pending.example.com/v1"}
        session.save()

        response = self.client.post(
            self.partial_url,
            self._payload(action="cancel_reembed"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")
        self.assertNotIn("memory_embeddings_pending", self.client.session)

    def test_cancel_reembed_without_htmx_renders_page(self):
        session = self.client.session
        session["memory_embeddings_pending"] = {"memory_embeddings_url": "https://pending.example.com/v1"}
        session.save()

        response = self.client.post(
            self.url,
            self._payload(action="cancel_reembed"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("memory_embeddings_pending", self.client.session)
        self.assertIn("Embeddings settings change cancelled.", self._messages(response))

    def test_confirm_reembed_without_pending_htmx_refreshes(self):
        response = self.client.post(
            self.partial_url,
            self._payload(action="confirm_reembed"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")

    def test_confirm_reembed_without_pending_renders_page(self):
        response = self.client.post(
            self.url,
            self._payload(action="confirm_reembed"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("No pending embeddings change to confirm.", self._messages(response))

    @patch("user_settings.views.memory.rebuild_user_memory_embeddings_task.delay")
    def test_confirm_reembed_applies_pending_settings_and_enqueues_rebuild(
        self,
        mocked_delay,
    ):
        session = self.client.session
        session["memory_embeddings_pending"] = {
            "memory_embeddings_enabled": True,
            "memory_embeddings_url": "https://new.example.com/v1",
            "memory_embeddings_model": "embed-large",
            "memory_embeddings_api_key": "new-secret",
        }
        session.save()

        response = self.client.post(
            self.url,
            self._payload(action="confirm_reembed"),
        )

        self.assertEqual(response.status_code, 200)
        self.parameters.refresh_from_db()
        self.assertTrue(self.parameters.memory_embeddings_enabled)
        self.assertEqual(self.parameters.memory_embeddings_url, "https://new.example.com/v1")
        self.assertEqual(self.parameters.memory_embeddings_model, "embed-large")
        self.assertEqual(self.parameters.memory_embeddings_api_key, "new-secret")
        self.assertNotIn("memory_embeddings_pending", self.client.session)
        mocked_delay.assert_called_once_with(self.user.id)

    @patch("user_settings.views.memory.rebuild_user_memory_embeddings_task.delay")
    def test_confirm_reembed_htmx_refreshes_after_applying_settings(self, mocked_delay):
        session = self.client.session
        session["memory_embeddings_pending"] = {
            "memory_embeddings_enabled": True,
            "memory_embeddings_url": "https://new.example.com/v1",
            "memory_embeddings_model": "embed-large",
            "memory_embeddings_api_key": "new-secret",
        }
        session.save()

        response = self.client.post(
            self.partial_url,
            self._payload(action="confirm_reembed"),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")
        mocked_delay.assert_called_once_with(self.user.id)

    @patch("user_settings.views.memory.get_embeddings_provider")
    def test_test_embeddings_without_provider_shows_warning(self, mocked_get_provider):
        mocked_get_provider.return_value = None

        response = self.client.post(
            self.url,
            self._payload(action="test_embeddings"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Embeddings provider is not configured.", self._messages(response))

    @patch("user_settings.views.memory.get_embeddings_provider")
    @patch("user_settings.views.memory.compute_embedding", new_callable=AsyncMock)
    def test_test_embeddings_success_with_typed_provider_refreshes_htmx(
        self,
        mocked_compute_embedding,
        mocked_get_provider,
    ):
        mocked_compute_embedding.return_value = [0.1, 0.2, 0.3]
        mocked_get_provider.return_value = None

        response = self.client.post(
            self.partial_url,
            self._payload(
                enabled=True,
                url="https://embeddings.example.com/v1",
                model="embed-small",
                api_key="secret",
                action="test_embeddings",
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")
        mocked_get_provider.assert_not_called()

    @patch("user_settings.views.memory.compute_embedding", new_callable=AsyncMock)
    def test_test_embeddings_warns_when_provider_returns_no_vector(self, mocked_compute_embedding):
        mocked_compute_embedding.return_value = None

        response = self.client.post(
            self.url,
            self._payload(
                enabled=True,
                url="https://embeddings.example.com/v1",
                model="embed-small",
                action="test_embeddings",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "Embeddings provider returned no vector (disabled).",
            self._messages(response),
        )

    @patch("user_settings.views.memory.compute_embedding", new_callable=AsyncMock)
    def test_test_embeddings_handles_provider_exceptions(self, mocked_compute_embedding):
        mocked_compute_embedding.side_effect = RuntimeError("boom")

        response = self.client.post(
            self.url,
            self._payload(
                enabled=True,
                url="https://embeddings.example.com/v1",
                model="embed-small",
                action="test_embeddings",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Embeddings test failed: boom", self._messages(response))

    def test_invalid_form_renders_errors(self):
        response = self.client.post(
            self.partial_url,
            self._payload(url="x" * 401),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value has at most 400 characters")

    @patch("user_settings.views.memory.rebuild_user_memory_embeddings_task.delay")
    def test_provider_change_requires_confirmation_and_stores_pending_session(
        self,
        mocked_delay,
    ):
        self.parameters.memory_embeddings_enabled = True
        self.parameters.memory_embeddings_url = "https://old.example.com/v1"
        self.parameters.memory_embeddings_model = "embed-old"
        self.parameters.memory_embeddings_api_key = "old-secret"
        self.parameters.save()
        self._create_embedding()

        response = self.client.post(
            self.partial_url,
            self._payload(
                enabled=True,
                url="https://new.example.com/v1",
                model="embed-new",
                api_key="new-secret",
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")
        self.parameters.refresh_from_db()
        self.assertEqual(self.parameters.memory_embeddings_url, "https://old.example.com/v1")
        pending = self.client.session["memory_embeddings_pending"]
        self.assertEqual(pending["memory_embeddings_url"], "https://new.example.com/v1")
        self.assertEqual(pending["memory_embeddings_model"], "embed-new")
        mocked_delay.assert_not_called()

    def test_provider_change_requires_confirmation_renders_page_without_htmx(self):
        self.parameters.memory_embeddings_enabled = True
        self.parameters.memory_embeddings_url = "https://old.example.com/v1"
        self.parameters.memory_embeddings_model = "embed-old"
        self.parameters.save()

        response = self.client.post(
            self.url,
            self._payload(
                enabled=True,
                url="https://new.example.com/v1",
                model="embed-new",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("memory_embeddings_pending", self.client.session)

    def test_immediate_save_updates_settings_without_confirmation(self):
        response = self.client.post(
            self.url,
            self._payload(
                enabled=True,
                url="https://same.example.com/v1",
                model="embed-same",
                api_key="secret",
                confirm=1,
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.parameters.refresh_from_db()
        self.assertTrue(self.parameters.memory_embeddings_enabled)
        self.assertEqual(self.parameters.memory_embeddings_url, "https://same.example.com/v1")
        self.assertEqual(self.parameters.memory_embeddings_model, "embed-same")
        self.assertEqual(self.parameters.memory_embeddings_api_key, "secret")
        self.assertIn("Memory settings updated successfully", self._messages(response))

    def test_immediate_save_htmx_returns_refresh(self):
        response = self.client.post(
            self.partial_url,
            self._payload(confirm=1),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")
