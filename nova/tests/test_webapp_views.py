from __future__ import annotations

from unittest.mock import AsyncMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.models.WebApp import WebApp

User = get_user_model()


class WebAppViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="webapp-view-user", password="pass")
        self.other = User.objects.create_user(username="webapp-view-other", password="pass")
        self.thread = Thread.objects.create(user=self.user, subject="View thread")
        self.other_thread = Thread.objects.create(user=self.other, subject="Other view thread")
        self.client.login(username="webapp-view-user", password="pass")
        self._stored_contents: dict[str, bytes] = {}

        async def fake_download_file_content(user_file):
            return self._stored_contents.get(user_file.key, b"")

        self.download_patcher = patch("nova.webapp.service.download_file_content", new=fake_download_file_content)
        self.download_patcher.start()
        self.addCleanup(self.download_patcher.stop)

    def _create_live_webapp(
        self,
        *,
        thread: Thread | None = None,
        user=None,
        name: str = "",
        slug: str | None = None,
        source_root: str = "/webapps/demo",
        entry_path: str = "index.html",
        files: dict[str, bytes] | None = None,
    ) -> WebApp:
        owner = user or self.user
        app_thread = thread or self.thread
        app = WebApp.objects.create(
            user=owner,
            thread=app_thread,
            name=name,
            slug=slug or WebApp._meta.get_field("slug").default(),
            source_root=source_root,
            entry_path=entry_path,
        )
        for relative_path, content in (files or {"index.html": b"<h1>App</h1>"}).items():
            full_path = f"{source_root.rstrip('/')}/{relative_path}"
            key = f"fake://{owner.id}/{app_thread.id}/{full_path.lstrip('/')}"
            self._stored_contents[key] = bytes(content)
            if relative_path.endswith(".html"):
                mime_type = "text/html"
            elif relative_path.endswith(".css"):
                mime_type = "text/css"
            elif relative_path.endswith(".js"):
                mime_type = "application/javascript"
            else:
                mime_type = "application/octet-stream"
            UserFile.objects.create(
                user=owner,
                thread=app_thread,
                key=key,
                original_filename=full_path,
                mime_type=mime_type,
                size=len(content),
                scope=UserFile.Scope.THREAD_SHARED,
            )
        return app

    def test_webapps_list_renders_name_and_slug_fallback(self):
        named = self._create_live_webapp(name="Invoices Dashboard", source_root="/webapps/named")
        legacy = self._create_live_webapp(name="", source_root="/webapps/legacy")

        response = self.client.get(reverse("webapps_list", args=[self.thread.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        self.assertIn("Invoices Dashboard", html)
        self.assertIn(named.slug, html)
        self.assertIn(legacy.slug, html)

    def test_delete_webapp_success_and_realtime_publish(self):
        app = self._create_live_webapp(name="Delete me")

        with patch("nova.webapp.service.publish_webapp_update", new_callable=AsyncMock) as mocked_publish:
            response = self.client.delete(reverse("delete_webapp", args=[self.thread.id, app.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "deleted")
        self.assertFalse(WebApp.objects.filter(id=app.id).exists())
        mocked_publish.assert_awaited_once()

    def test_delete_webapp_denies_other_user(self):
        other_app = self._create_live_webapp(
            thread=self.other_thread,
            user=self.other,
            name="Other app",
            source_root="/webapps/other",
        )

        response = self.client.delete(reverse("delete_webapp", args=[self.other_thread.id, other_app.slug]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WebApp.objects.filter(id=other_app.id).exists())

    def test_delete_webapp_denies_wrong_thread_scope(self):
        same_user_other_thread = Thread.objects.create(user=self.user, subject="Second thread")
        app = self._create_live_webapp(thread=same_user_other_thread, name="Scoped app", source_root="/webapps/scoped")

        response = self.client.delete(reverse("delete_webapp", args=[self.thread.id, app.slug]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WebApp.objects.filter(id=app.id).exists())

    def test_preview_webapp_includes_mobile_message_context_menu(self):
        app = self._create_live_webapp(name="Preview app", source_root="/webapps/preview")

        response = self.client.get(reverse("preview_webapp", args=[self.thread.id, app.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="messageContextMenu"')
        self.assertContains(response, 'id="context-menu-execution-details"')
        self.assertContains(response, 'id="context-menu-compact"')
        self.assertContains(response, 'id="context-menu-delete-after"')

    def test_deleted_webapp_is_not_served_anymore(self):
        app = self._create_live_webapp(name="Temporary app", source_root="/webapps/temp")
        self.assertEqual(self.client.get(reverse("serve_webapp_root", args=[app.slug])).status_code, 200)

        self.client.delete(reverse("delete_webapp", args=[self.thread.id, app.slug]))

        self.assertEqual(self.client.get(reverse("serve_webapp_root", args=[app.slug])).status_code, 404)

    def test_serve_webapp_root_and_nested_asset_are_served_live_from_user_files(self):
        app = self._create_live_webapp(
            name="Live app",
            source_root="/webapps/live",
            files={
                "index.html": b"<h1>Main</h1>",
                "assets/app.js": b"console.log('live');",
            },
        )

        root_response = self.client.get(reverse("serve_webapp_root", args=[app.slug]))
        asset_response = self.client.get(reverse("serve_webapp_file", args=[app.slug, "assets/app.js"]))

        self.assertEqual(root_response.status_code, 200)
        self.assertIn("<h1>Main</h1>", root_response.content.decode("utf-8"))
        self.assertEqual(asset_response.status_code, 200)
        self.assertIn("application/javascript", asset_response["Content-Type"])
        self.assertEqual(
            root_response["Content-Security-Policy"],
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'none'; "
            "frame-ancestors 'self'; "
            "form-action 'self';",
        )

    def test_serve_webapp_returns_404_when_entry_file_disappears(self):
        app = self._create_live_webapp(name="Broken app", source_root="/webapps/broken")
        UserFile.objects.filter(
            user=self.user,
            thread=self.thread,
            original_filename="/webapps/broken/index.html",
        ).delete()

        response = self.client.get(reverse("serve_webapp_root", args=[app.slug]))
        self.assertEqual(response.status_code, 404)

    def test_serve_webapp_rejects_extensions_outside_allowlist(self):
        app = self._create_live_webapp(
            name="App",
            source_root="/webapps/allowlist",
            files={
                "index.html": b"<h1>Main</h1>",
                "secrets.py": b"print('no')",
            },
        )

        response = self.client.get(reverse("serve_webapp_file", args=[app.slug, "secrets.py"]))
        self.assertEqual(response.status_code, 404)
