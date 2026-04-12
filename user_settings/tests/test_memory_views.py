from unittest.mock import AsyncMock, Mock, patch

from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.contrib.messages.views import SuccessMessageMixin
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from nova.models.MemoryChunk import MemoryChunk
from nova.models.MemoryChunkEmbedding import MemoryChunkEmbedding
from nova.models.MemoryDocument import MemoryDocument
from nova.models.memory_common import MemoryRecordStatus
from nova.models.UserObjects import MemoryEmbeddingsSource, UserParameters
from nova.plugins.catalog import build_tools_page_catalog
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
        source=MemoryEmbeddingsSource.SYSTEM,
        url="",
        model="",
        api_key="",
        action=None,
        confirm=None,
    ):
        data = {
            "from": "memory",
            "memory_embeddings_source": source,
            "memory_embeddings_url": url,
            "memory_embeddings_model": model,
            "memory_embeddings_api_key": api_key,
        }
        if action:
            data["action"] = action
        if confirm is not None:
            data["confirm"] = str(confirm)
        return data

    def _create_embedding(self, *, content="remember this"):
        document = MemoryDocument.objects.create(
            user=self.user,
            virtual_path=f"/memory/{content.replace(' ', '-')}.md",
            title="Memory",
            content_markdown=f"# Memory\n\n{content}",
            status=MemoryRecordStatus.ACTIVE,
        )
        chunk = MemoryChunk.objects.create(
            document=document,
            heading="Memory",
            anchor="memory",
            position=0,
            content_text=content,
            token_count=len(content.split()),
            status=MemoryRecordStatus.ACTIVE,
        )
        return MemoryChunkEmbedding.objects.create(chunk=chunk)

    def _messages(self, response):
        return [message.message for message in get_messages(response.wsgi_request)]

    def test_get_partial_prefills_pending_values_and_context(self):
        pending = {
            "memory_embeddings_source": MemoryEmbeddingsSource.CUSTOM,
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

        response = self.client.get(self.partial_url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/fragments/memory_form.html")
        self.assertEqual(UserParameters.objects.filter(user=self.user).count(), 1)
        self.assertTrue(response.context["has_pending_reembed"])
        self.assertEqual(response.context["pending_reembed_count"], 2)
        self.assertContains(response, 'value="https://pending.example.com/v1"', html=False)
        self.assertContains(response, "Effective provider")
        self.assertContains(response, "Custom embeddings provider")

    def test_get_full_page_uses_full_template(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "user_settings/memory_form.html")
        self.assertContains(response, "Semantic retrieval settings")
        self.assertContains(response, "Memory records")
        self.assertContains(response, "No agent currently has the Memory capability enabled.")

    def test_memory_browser_fragment_loads_immediately_when_memory_form_renders(self):
        response = self.client.get(self.partial_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="memory-items-container"', html=False)
        self.assertContains(response, 'hx-trigger="load"', html=False)

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
        session["memory_embeddings_pending"] = {"memory_embeddings_source": MemoryEmbeddingsSource.CUSTOM}
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
        session["memory_embeddings_pending"] = {"memory_embeddings_source": MemoryEmbeddingsSource.CUSTOM}
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
    @patch("user_settings.views.memory.rebuild_user_conversation_embeddings_task.delay")
    def test_confirm_reembed_applies_pending_settings_and_enqueues_rebuilds(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        session = self.client.session
        session["memory_embeddings_pending"] = {
            "memory_embeddings_source": MemoryEmbeddingsSource.CUSTOM,
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
        self.assertEqual(self.parameters.memory_embeddings_source, MemoryEmbeddingsSource.CUSTOM)
        self.assertEqual(self.parameters.memory_embeddings_url, "https://new.example.com/v1")
        self.assertEqual(self.parameters.memory_embeddings_model, "embed-large")
        self.assertEqual(self.parameters.memory_embeddings_api_key, "new-secret")
        self.assertNotIn("memory_embeddings_pending", self.client.session)
        mocked_memory_delay.assert_called_once_with(self.user.id)
        mocked_conversation_delay.assert_called_once_with(self.user.id)

    @patch("user_settings.views.memory.rebuild_user_memory_embeddings_task.delay")
    @patch("user_settings.views.memory.rebuild_user_conversation_embeddings_task.delay")
    def test_confirm_reembed_htmx_refreshes_after_applying_settings(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        session = self.client.session
        session["memory_embeddings_pending"] = {
            "memory_embeddings_source": MemoryEmbeddingsSource.CUSTOM,
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
        mocked_memory_delay.assert_called_once_with(self.user.id)
        mocked_conversation_delay.assert_called_once_with(self.user.id)

    def test_test_embeddings_without_system_provider_shows_warning(self):
        response = self.client.post(
            self.url,
            self._payload(
                source=MemoryEmbeddingsSource.SYSTEM,
                action="test_embeddings",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("No system embeddings provider is configured.", self._messages(response)[0])

    @patch("user_settings.views.memory.compute_embedding", new_callable=AsyncMock)
    def test_test_embeddings_success_with_typed_custom_provider_refreshes_htmx(
        self,
        mocked_compute_embedding,
    ):
        mocked_compute_embedding.return_value = [0.1, 0.2, 0.3]

        response = self.client.post(
            self.partial_url,
            self._payload(
                source=MemoryEmbeddingsSource.CUSTOM,
                url="https://embeddings.example.com/v1",
                model="embed-small",
                api_key="secret",
                action="test_embeddings",
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.headers.get("HX-Refresh"), "true")

    def test_test_embeddings_warns_when_disabled(self):
        response = self.client.post(
            self.url,
            self._payload(
                source=MemoryEmbeddingsSource.DISABLED,
                action="test_embeddings",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("Embeddings are disabled for memory.", self._messages(response))

    @patch("user_settings.views.memory.compute_embedding", new_callable=AsyncMock)
    def test_test_embeddings_warns_when_provider_returns_no_vector(self, mocked_compute_embedding):
        mocked_compute_embedding.return_value = None

        response = self.client.post(
            self.url,
            self._payload(
                source=MemoryEmbeddingsSource.CUSTOM,
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
                source=MemoryEmbeddingsSource.CUSTOM,
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
            self._payload(
                source=MemoryEmbeddingsSource.CUSTOM,
                url="x" * 401,
            ),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ensure this value has at most 400 characters")

    @patch("user_settings.views.memory.rebuild_user_memory_embeddings_task.delay")
    @patch("user_settings.views.memory.rebuild_user_conversation_embeddings_task.delay")
    def test_custom_provider_change_requires_confirmation_and_stores_pending_session(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        self.parameters.memory_embeddings_source = MemoryEmbeddingsSource.CUSTOM
        self.parameters.memory_embeddings_url = "https://old.example.com/v1"
        self.parameters.memory_embeddings_model = "embed-old"
        self.parameters.memory_embeddings_api_key = "old-secret"
        self.parameters.save()
        self._create_embedding()

        response = self.client.post(
            self.partial_url,
            self._payload(
                source=MemoryEmbeddingsSource.CUSTOM,
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
        self.assertEqual(pending["memory_embeddings_source"], MemoryEmbeddingsSource.CUSTOM)
        self.assertEqual(pending["memory_embeddings_url"], "https://new.example.com/v1")
        self.assertEqual(pending["memory_embeddings_model"], "embed-new")
        mocked_memory_delay.assert_not_called()
        mocked_conversation_delay.assert_not_called()

    @patch("user_settings.views.memory.rebuild_user_memory_embeddings_task.delay")
    @patch("user_settings.views.memory.rebuild_user_conversation_embeddings_task.delay")
    def test_switching_from_disabled_to_custom_saves_and_rebuilds_without_confirmation(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        self.parameters.memory_embeddings_source = MemoryEmbeddingsSource.DISABLED
        self.parameters.save(update_fields=["memory_embeddings_source"])

        response = self.client.post(
            self.url,
            self._payload(
                source=MemoryEmbeddingsSource.CUSTOM,
                url="https://same.example.com/v1",
                model="embed-same",
                api_key="secret",
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.parameters.refresh_from_db()
        self.assertEqual(self.parameters.memory_embeddings_source, MemoryEmbeddingsSource.CUSTOM)
        self.assertEqual(self.parameters.memory_embeddings_url, "https://same.example.com/v1")
        self.assertEqual(self.parameters.memory_embeddings_model, "embed-same")
        self.assertEqual(self.parameters.memory_embeddings_api_key, "secret")
        mocked_memory_delay.assert_called_once_with(self.user.id)
        mocked_conversation_delay.assert_called_once_with(self.user.id)
        self.assertIn(
            "Embeddings settings updated. Rebuilding embeddings in background.",
            self._messages(response),
        )

    @override_settings(
        MEMORY_EMBEDDINGS_URL="https://system.example.com/v1",
        MEMORY_EMBEDDINGS_MODEL="embed-system",
    )
    @patch("nova.tasks.memory_rebuild_tasks.rebuild_user_memory_embeddings_task.delay")
    @patch("nova.tasks.conversation_embedding_tasks.rebuild_user_conversation_embeddings_task.delay")
    def test_page_displays_system_provider_details_when_available(
        self,
        mocked_conversation_delay,
        mocked_memory_delay,
    ):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "System embeddings provider")
        self.assertContains(response, "https://system.example.com/v1")
        self.assertContains(response, "embed-system")


class MemoryDashboardAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory-dashboard-user",
            email="memory-dashboard@example.com",
            password="pass123",
        )
        self.client.login(username="memory-dashboard-user", password="pass123")
        self.url = reverse("user_settings:dashboard")

    def test_dashboard_always_shows_memory_tab(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="tab-memory"', html=False)
        self.assertContains(response, 'data-bs-target="#pane-memory"', html=False)
        self.assertContains(
            response,
            f'hx-get="{reverse("user_settings:memory")}?partial=1"',
            html=False,
        )


class MemoryToolsCatalogTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="memory-tools-user",
            email="memory-tools@example.com",
            password="pass123",
        )
        self.client.login(username="memory-tools-user", password="pass123")
        self.url = reverse("user_settings:tools")
        self.parameters, _ = UserParameters.objects.get_or_create(user=self.user)

    def _memory_capability(self):
        catalog = build_tools_page_catalog(self.user)
        return next(item for item in catalog["built_in_capabilities"] if item["label"] == "Memory")

    def test_tools_page_exposes_memory_settings_shortcut(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Open memory settings")
        self.assertContains(
            response,
            f'href="{reverse("user_settings:dashboard")}#pane-memory"',
            html=False,
        )
        self.assertContains(response, "Lexical only")

    def test_memory_capability_status_summary_tracks_user_embeddings_mode(self):
        capability = self._memory_capability()
        self.assertEqual(capability["status_summary"]["value"], "Lexical only")

        self.parameters.memory_embeddings_source = MemoryEmbeddingsSource.DISABLED
        self.parameters.save(update_fields=["memory_embeddings_source"])
        capability = self._memory_capability()
        self.assertEqual(capability["status_summary"]["value"], "Embeddings disabled")

        self.parameters.memory_embeddings_source = MemoryEmbeddingsSource.CUSTOM
        self.parameters.memory_embeddings_url = "https://embeddings.example.com/v1"
        self.parameters.memory_embeddings_model = "embed-1"
        self.parameters.save(
            update_fields=[
                "memory_embeddings_source",
                "memory_embeddings_url",
                "memory_embeddings_model",
            ]
        )
        capability = self._memory_capability()
        self.assertEqual(capability["status_summary"]["value"], "Semantic search ready")
