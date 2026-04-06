from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.models.WebApp import WebApp
from nova.runtime_v2.vfs import VirtualFileSystem
from nova.webapp.service import (
    WebAppServiceError,
    describe_webapp,
    expose_webapp,
    get_live_file_for_webapp,
    list_thread_webapps,
    maybe_touch_impacted_webapps,
)

User = get_user_model()


class WebAppServiceTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="webapp-service-user", password="pass")
        self.thread = Thread.objects.create(user=self.user, subject="Service thread")
        self.agent = SimpleNamespace(id=77)
        self.vfs = VirtualFileSystem(
            thread=self.thread,
            user=self.user,
            agent_config=self.agent,
            session_state={"cwd": "/", "history": [], "directories": ["/tmp"]},
            skill_registry={},
        )

    def _create_thread_file(self, path: str, content: bytes = b"<h1>Hello</h1>", mime_type: str = "text/html") -> UserFile:
        return UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=f"fake://{self.thread.id}/{path.lstrip('/')}",
            original_filename=path,
            mime_type=mime_type,
            size=len(content),
            scope=UserFile.Scope.THREAD_SHARED,
        )

    def test_expose_webapp_creates_live_publication_from_index_html(self):
        self._create_thread_file("/webapps/demo/index.html")
        self._create_thread_file("/webapps/demo/app.css", content=b"body{}", mime_type="text/css")

        payload = async_to_sync(expose_webapp)(
            user=self.user,
            thread=self.thread,
            vfs=self.vfs,
            source_root="/webapps/demo",
            name="Demo App",
        )

        webapp = WebApp.objects.get(thread=self.thread)
        self.assertTrue(payload["created"])
        self.assertEqual(payload["slug"], webapp.slug)
        self.assertEqual(webapp.source_root, "/webapps/demo")
        self.assertEqual(webapp.entry_path, "index.html")
        self.assertEqual(webapp.name, "Demo App")

    def test_expose_webapp_update_reuses_existing_slug(self):
        self._create_thread_file("/webapps/demo/index.html")
        existing = WebApp.objects.create(
            user=self.user,
            thread=self.thread,
            name="Old",
            source_root="/webapps/demo",
            entry_path="index.html",
        )

        payload = async_to_sync(expose_webapp)(
            user=self.user,
            thread=self.thread,
            vfs=self.vfs,
            source_root="/webapps/demo",
            slug=existing.slug,
            name="Renamed",
        )

        existing.refresh_from_db()
        self.assertFalse(payload["created"])
        self.assertEqual(existing.name, "Renamed")

    def test_expose_webapp_rejects_forbidden_root(self):
        with self.assertRaises(WebAppServiceError):
            async_to_sync(expose_webapp)(
                user=self.user,
                thread=self.thread,
                vfs=self.vfs,
                source_root="/tmp/demo",
            )

    def test_expose_webapp_requires_explicit_entry_when_root_has_multiple_html_files(self):
        self._create_thread_file("/webapps/ambiguous/a.html")
        self._create_thread_file("/webapps/ambiguous/b.html")

        with self.assertRaises(WebAppServiceError) as cm:
            async_to_sync(expose_webapp)(
                user=self.user,
                thread=self.thread,
                vfs=self.vfs,
                source_root="/webapps/ambiguous",
            )

        self.assertIn("--entry", str(cm.exception))

    def test_list_and_describe_webapps_use_live_payload(self):
        self._create_thread_file("/webapps/demo/index.html")
        webapp = WebApp.objects.create(
            user=self.user,
            thread=self.thread,
            name="Demo",
            source_root="/webapps/demo",
            entry_path="index.html",
        )

        listing = async_to_sync(list_thread_webapps)(user=self.user, thread=self.thread)
        details = async_to_sync(describe_webapp)(user=self.user, thread=self.thread, slug=webapp.slug)

        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["slug"], webapp.slug)
        self.assertEqual(details["source_root"], "/webapps/demo")
        self.assertEqual(details["status"], "ready")

    def test_get_live_file_for_webapp_resolves_nested_assets(self):
        self._create_thread_file("/webapps/demo/index.html")
        asset = self._create_thread_file(
            "/webapps/demo/assets/app.js",
            content=b"console.log('ok');",
            mime_type="application/javascript",
        )
        webapp = WebApp.objects.create(
            user=self.user,
            thread=self.thread,
            name="Demo",
            source_root="/webapps/demo",
            entry_path="index.html",
        )

        live_file = get_live_file_for_webapp(user=self.user, slug=webapp.slug, requested_path="assets/app.js")

        self.assertIsNotNone(live_file)
        self.assertEqual(live_file.user_file.id, asset.id)
        self.assertEqual(live_file.relative_path, "assets/app.js")

    def test_touch_impacted_webapps_updates_source_root_when_directory_moves(self):
        WebApp.objects.create(
            user=self.user,
            thread=self.thread,
            name="Demo",
            source_root="/webapps/demo",
            entry_path="index.html",
        )

        with patch("nova.webapp.service.publish_webapp_update", new_callable=AsyncMock) as mocked_publish:
            slugs = async_to_sync(maybe_touch_impacted_webapps)(
                thread=self.thread,
                paths=["/webapps/demo", "/sites/demo"],
                moved_from="/webapps/demo",
                moved_to="/sites/demo",
            )

        webapp = WebApp.objects.get(thread=self.thread)
        self.assertEqual(slugs, [webapp.slug])
        self.assertEqual(webapp.source_root, "/sites/demo")
        mocked_publish.assert_awaited_once()
