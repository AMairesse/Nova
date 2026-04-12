from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.models.WebApp import WebApp
from nova.runtime.vfs import VirtualFileSystem
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
        self._stored_contents: dict[str, bytes] = {}
        self.vfs = VirtualFileSystem(
            thread=self.thread,
            user=self.user,
            agent_config=self.agent,
            session_state={"cwd": "/", "history": [], "directories": ["/tmp"]},
            skill_registry={},
        )
        self.download_patcher = patch(
            "nova.webapp.service.download_file_content",
            new=self._fake_download_file_content,
        )
        self.vfs_download_patcher = patch(
            "nova.runtime.vfs.download_file_content",
            new=self._fake_download_file_content,
        )
        self.download_patcher.start()
        self.vfs_download_patcher.start()
        self.addCleanup(self.download_patcher.stop)
        self.addCleanup(self.vfs_download_patcher.stop)

    async def _fake_download_file_content(self, user_file):
        return self._stored_contents.get(user_file.key, b"")

    def _create_thread_file(self, path: str, content: bytes = b"<h1>Hello</h1>", mime_type: str = "text/html") -> UserFile:
        key = f"fake://{self.thread.id}/{path.lstrip('/')}"
        self._stored_contents[key] = bytes(content)
        return UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key=key,
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

    def test_expose_webapp_rejects_escaped_html_entry(self):
        self._create_thread_file(
            "/webapps/demo/index.html",
            content=b"&lt;!DOCTYPE html&gt;&lt;html&gt;",
            mime_type="text/html",
        )

        with self.assertRaises(WebAppServiceError) as cm:
            async_to_sync(expose_webapp)(
                user=self.user,
                thread=self.thread,
                vfs=self.vfs,
                source_root="/webapps/demo",
            )

        self.assertIn("Entry HTML appears escaped", str(cm.exception))

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

    def test_describe_marks_webapp_broken_when_entry_html_is_escaped(self):
        self._create_thread_file(
            "/webapps/demo/index.html",
            content=b"&lt;!DOCTYPE html&gt;&lt;html&gt;",
            mime_type="text/html",
        )
        webapp = WebApp.objects.create(
            user=self.user,
            thread=self.thread,
            name="Demo",
            source_root="/webapps/demo",
            entry_path="index.html",
        )

        details = async_to_sync(describe_webapp)(user=self.user, thread=self.thread, slug=webapp.slug)

        self.assertEqual(details["status"], "broken")
        self.assertIn("escaped", details["status_detail"].lower())

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
            events = async_to_sync(maybe_touch_impacted_webapps)(
                thread=self.thread,
                paths=["/webapps/demo", "/sites/demo"],
                moved_from="/webapps/demo",
                moved_to="/sites/demo",
            )

        webapp = WebApp.objects.get(thread=self.thread)
        self.assertEqual(events, [{"slug": webapp.slug, "reason": "webapp_update"}])
        self.assertEqual(webapp.source_root, "/sites/demo")
        mocked_publish.assert_awaited_once()

    def test_touch_impacted_webapps_deletes_publication_when_source_root_is_removed(self):
        webapp = WebApp.objects.create(
            user=self.user,
            thread=self.thread,
            name="Demo",
            source_root="/webapps/demo",
            entry_path="index.html",
        )

        with patch("nova.webapp.service.publish_webapp_update", new_callable=AsyncMock) as mocked_publish:
            events = async_to_sync(maybe_touch_impacted_webapps)(
                thread=self.thread,
                paths=["/webapps/demo"],
                deleted_roots=["/webapps/demo"],
            )

        self.assertEqual(events, [{"slug": webapp.slug, "reason": "webapp_delete"}])
        self.assertFalse(WebApp.objects.filter(id=webapp.id).exists())
        mocked_publish.assert_awaited_once()
