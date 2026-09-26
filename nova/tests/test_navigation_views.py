from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from nova.models.Message import Actor
from nova.models.Thread import Thread


class NavigationViewsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_user("alice", password="pass")
        self.other = User.objects.create_user("bob", password="pass")
        self.client.login(username="alice", password="pass")

    def test_search_is_scoped_and_returns_message_deep_link(self):
        thread = Thread.objects.create(user=self.user, subject="Project notes")
        message = thread.add_message("A private navigation passage", actor=Actor.USER)
        foreign = Thread.objects.create(user=self.other, subject="private navigation")
        foreign.add_message("foreign-only navigation marker", actor=Actor.USER)

        response = self.client.get(reverse("search"), {"q": "navigation"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"message_id={message.id}#message-{message.id}")
        self.assertNotContains(response, "foreign-only navigation marker")

    def test_archive_is_reversible_and_hidden_by_default(self):
        thread = Thread.objects.create(user=self.user, subject="Archive me")

        response = self.client.post(reverse("archive_thread", args=[thread.id]))

        self.assertEqual(response.status_code, 200)
        thread.refresh_from_db()
        self.assertIsNotNone(thread.archived_at)
        self.assertNotContains(self.client.get(reverse("index")), "Archive me")
        self.assertContains(self.client.get(reverse("index"), {"archived": "1"}), "Archive me")

        response = self.client.post(reverse("unarchive_thread", args=[thread.id]))

        self.assertEqual(response.status_code, 200)
        thread.refresh_from_db()
        self.assertIsNone(thread.archived_at)

    def test_message_list_exposes_stable_pagination_position(self):
        thread = Thread.objects.create(user=self.user, subject="Paged")
        messages = [thread.add_message(str(i), actor=Actor.USER) for i in range(3)]

        response = self.client.get(reverse("message_list"), {
            "thread_id": thread.id, "offset": 1, "limit": 1,
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-message-offset="1"')
        self.assertContains(response, f"message-{messages[1].id}")
        self.assertContains(response, 'data-messages-has-more="true"')

    def test_navigation_reenables_older_messages_button_after_success(self):
        source = (Path(__file__).parents[1] / "static/js/navigation.js").read_text()
        self.assertIn("button.disabled = false", source)
        self.assertIn("if (hasMore)", source)

    def test_search_paginates_capped_result_set(self):
        for index in range(50):
            Thread.objects.create(user=self.user, subject=f"Paged navigation {index}")
        extra = Thread.objects.create(user=self.user, subject="Unrelated title")
        extra.add_message("Paged navigation extra", actor=Actor.USER)

        response = self.client.get(reverse("search"), {"q": "Paged navigation", "page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["page"].object_list), 1)
