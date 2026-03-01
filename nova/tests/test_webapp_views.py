from __future__ import annotations

from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.Thread import Thread
from nova.models.WebApp import WebApp
from nova.models.WebAppFile import WebAppFile

User = get_user_model()


class WebAppViewsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="webapp-view-user", password="pass")
        self.other = User.objects.create_user(username="webapp-view-other", password="pass")
        self.thread = Thread.objects.create(user=self.user, subject="View thread")
        self.other_thread = Thread.objects.create(user=self.other, subject="Other view thread")
        self.client.login(username="webapp-view-user", password="pass")

    def _create_webapp(self, *, thread: Thread | None = None, user=None, name: str = "", slug: str | None = None) -> WebApp:
        owner = user or self.user
        app_thread = thread or self.thread
        app = WebApp.objects.create(
            user=owner,
            thread=app_thread,
            name=name,
            slug=slug or WebApp._meta.get_field("slug").default(),
        )
        WebAppFile.objects.create(webapp=app, path="index.html", content="<h1>App</h1>")
        return app

    def test_webapps_list_renders_name_and_slug_fallback(self):
        named = self._create_webapp(name="Invoices Dashboard")
        legacy = self._create_webapp(name="")

        response = self.client.get(reverse("webapps_list", args=[self.thread.id]))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        self.assertIn("Invoices Dashboard", html)
        self.assertIn(named.slug, html)
        # Legacy app falls back to slug for display.
        self.assertIn(legacy.slug, html)

    def test_delete_webapp_success_and_realtime_publish(self):
        app = self._create_webapp(name="Delete me")

        with patch("nova.views.webapp_views.async_to_sync") as mocked_async_to_sync:
            mocked_runner = Mock()
            mocked_async_to_sync.return_value = mocked_runner
            response = self.client.delete(reverse("delete_webapp", args=[self.thread.id, app.slug]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertFalse(WebApp.objects.filter(id=app.id).exists())
        mocked_runner.assert_called_once_with(self.thread.id, "webapp_delete", slug=app.slug)

    def test_delete_webapp_denies_other_user(self):
        other_app = self._create_webapp(thread=self.other_thread, user=self.other, name="Other app")

        response = self.client.delete(reverse("delete_webapp", args=[self.other_thread.id, other_app.slug]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WebApp.objects.filter(id=other_app.id).exists())

    def test_delete_webapp_denies_wrong_thread_scope(self):
        same_user_other_thread = Thread.objects.create(user=self.user, subject="Second thread")
        app = self._create_webapp(thread=same_user_other_thread, name="Scoped app")

        response = self.client.delete(reverse("delete_webapp", args=[self.thread.id, app.slug]))
        self.assertEqual(response.status_code, 404)
        self.assertTrue(WebApp.objects.filter(id=app.id).exists())

    def test_deleted_webapp_is_not_served_anymore(self):
        app = self._create_webapp(name="Temporary app")
        self.assertEqual(self.client.get(reverse("serve_webapp_root", args=[app.slug])).status_code, 200)

        self.client.delete(reverse("delete_webapp", args=[self.thread.id, app.slug]))

        self.assertEqual(self.client.get(reverse("serve_webapp_root", args=[app.slug])).status_code, 404)

    def test_serve_webapp_root_falls_back_to_first_html_when_index_missing(self):
        app = WebApp.objects.create(
            user=self.user,
            thread=self.thread,
            name="No index app",
            slug=WebApp._meta.get_field("slug").default(),
        )
        WebAppFile.objects.create(webapp=app, path="main.html", content="<h1>Main</h1>")
        WebAppFile.objects.create(webapp=app, path="styles.css", content="body{}")

        response = self.client.get(reverse("serve_webapp_root", args=[app.slug]))
        self.assertEqual(response.status_code, 200)
        self.assertIn("<h1>Main</h1>", response.content.decode("utf-8"))
