# nova/tests/test_models.py
from django.test import TestCase
from django.contrib.auth.models import User

from nova.models import Thread, Message, Actor


class ThreadModelTest(TestCase):
    def setUp(self) -> None:
        # create_user hashes the password correctly (create() would not)
        self.user = User.objects.create_user("alice", password="pwd")

    # ------------------------------------------------------------------ #
    #  Basic creation                                                    #
    # ------------------------------------------------------------------ #
    def test_create_thread(self):
        """A Thread can be created and its __str__ matches the subject."""
        thread = Thread.objects.create(subject="Test Subject", user=self.user)

        self.assertEqual(str(thread), "Test Subject")
        self.assertIsNotNone(thread.created_at)

    # ------------------------------------------------------------------ #
    #  add_message helper                                                #
    # ------------------------------------------------------------------ #
    def test_add_message(self):
        """add_message stores the text, actor and FK relations properly."""
        thread = Thread.objects.create(subject="Test Subject", user=self.user)

        message_text = "This is a test message"
        msg = thread.add_message(message_text, actor=Actor.USER)

        self.assertEqual(Message.objects.count(), 1)
        self.assertEqual(msg.text, message_text)
        self.assertEqual(msg.actor, Actor.USER)
        self.assertEqual(msg.thread, thread)
        self.assertEqual(str(msg), message_text)

    # ------------------------------------------------------------------ #
    #  get_messages helper                                               #
    # ------------------------------------------------------------------ #
    def test_get_messages(self):
        """get_messages returns all messages belonging to the thread."""
        thread = Thread.objects.create(subject="Test Subject", user=self.user)
        thread.add_message("Message One", actor=Actor.USER)
        thread.add_message("Message Two", actor=Actor.USER)

        messages = thread.get_messages()

        self.assertEqual(messages.count(), 2)
        self.assertQuerySetEqual(
            messages.order_by("id").values_list("text", flat=True),
            ["Message One", "Message Two"],
            ordered=True,
        )
