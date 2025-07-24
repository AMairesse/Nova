# nova/tests/test_thread_model.py
"""
Tests for the Thread model and its helper methods.

Focus on Thread-specific behavior:
- add_message() with various actors
- get_messages() filtering
- message ordering
"""

from django.test import TestCase
from django.contrib.auth.models import User
from django.db.models.query import QuerySet

from nova.models import Thread, Message, Actor


class ThreadModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        
        # Create a test thread
        self.thread = Thread.objects.create(
            user=self.user,
            subject="Test Thread"
        )

    def test_thread_str_representation(self):
        """Test the string representation of a Thread"""
        self.assertEqual(str(self.thread), "Test Thread")

    def test_add_message_with_user_actor(self):
        """Test adding a message with USER actor"""
        message = self.thread.add_message("Test message", actor=Actor.USER)
        
        # Verify the message was created
        self.assertEqual(message.text, "Test message")
        self.assertEqual(message.actor, Actor.USER)
        self.assertEqual(message.thread, self.thread)
        self.assertEqual(message.user, self.user)
        
        # Verify the message is in the database
        db_message = Message.objects.get(id=message.id)
        self.assertEqual(db_message.text, "Test message")
        self.assertEqual(db_message.actor, Actor.USER)

    def test_add_message_with_agent_actor(self):
        """Test adding a message with AGENT actor"""
        message = self.thread.add_message("Agent message", actor=Actor.AGENT)
        
        # Verify the message was created with the correct actor
        self.assertEqual(message.text, "Agent message")
        self.assertEqual(message.actor, Actor.AGENT)
        self.assertEqual(message.thread, self.thread)
        self.assertEqual(message.user, self.user)

    def test_add_message_with_invalid_actor_string(self):
        """Test adding a message with an invalid actor string raises ValueError"""
        with self.assertRaises(ValueError) as cm:
            self.thread.add_message("Invalid actor message", actor="InvalidActor")
        
        self.assertIn("Invalid actor", str(cm.exception))

    def test_add_message_with_invalid_actor_none(self):
        """Test adding a message with None actor raises ValueError"""
        with self.assertRaises(ValueError) as cm:
            self.thread.add_message("None actor message", actor=None)
        
        self.assertIn("Invalid actor", str(cm.exception))

    def test_get_messages_empty_thread(self):
        """Test getting messages from an empty thread"""
        # Create a new empty thread
        empty_thread = Thread.objects.create(
            user=self.user,
            subject="Empty Thread"
        )
        
        messages = empty_thread.get_messages()
        
        # Verify it returns an empty QuerySet
        self.assertIsInstance(messages, QuerySet)
        self.assertEqual(messages.count(), 0)

    def test_get_messages_with_messages(self):
        """Test getting messages from a thread with messages"""
        # Add some messages to the thread
        message1 = self.thread.add_message("First message", actor=Actor.USER)
        message2 = self.thread.add_message("Second message", actor=Actor.AGENT)
        
        messages = self.thread.get_messages()
        
        # Verify it returns all messages in the thread
        self.assertIsInstance(messages, QuerySet)
        self.assertEqual(messages.count(), 2)
        
        # Verify the messages are in the correct order (by creation time)
        message_list = list(messages.order_by('created_at'))
        self.assertEqual(message_list[0].id, message1.id)
        self.assertEqual(message_list[1].id, message2.id)

    def test_get_messages_only_returns_thread_messages(self):
        """Test that get_messages only returns messages for this specific thread"""
        # Create another thread with messages
        other_thread = Thread.objects.create(
            user=self.user,
            subject="Other Thread"
        )
        
        # Add messages to both threads
        self.thread.add_message("Thread 1 message", actor=Actor.USER)
        other_thread.add_message("Thread 2 message", actor=Actor.USER)
        
        # Get messages for the first thread
        messages = self.thread.get_messages()
        
        # Verify it only returns messages for the first thread
        self.assertEqual(messages.count(), 1)
        self.assertEqual(messages.first().text, "Thread 1 message")

    def test_get_messages_filters_by_user(self):
        """Test that get_messages only returns messages from the thread owner"""
        # Create another user
        other_user = User.objects.create_user(
            username='otheruser',
            password='otherpass'
        )
        
        # Add a message from the thread owner
        self.thread.add_message("Owner message", actor=Actor.USER)
        
        # Manually create a message from another user (edge case)
        Message.objects.create(
            thread=self.thread,
            user=other_user,
            text="Other user message",
            actor=Actor.USER
        )
        
        # get_messages should only return messages from the thread owner
        messages = self.thread.get_messages()
        self.assertEqual(messages.count(), 1)
        self.assertEqual(messages.first().text, "Owner message")

    def test_message_ordering_preserved(self):
        """Test that messages maintain chronological order"""
        import time
        
        # Add messages with slight delays to ensure different timestamps
        msg1 = self.thread.add_message("First", actor=Actor.USER)
        time.sleep(0.01)  # Small delay
        msg2 = self.thread.add_message("Second", actor=Actor.AGENT)
        time.sleep(0.01)
        msg3 = self.thread.add_message("Third", actor=Actor.USER)
        
        messages = list(self.thread.get_messages().order_by('created_at'))
        
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0].text, "First")
        self.assertEqual(messages[1].text, "Second")
        self.assertEqual(messages[2].text, "Third")

    def test_thread_cascade_delete(self):
        """Test that deleting a thread deletes all its messages"""
        # Add messages
        self.thread.add_message("Message 1", actor=Actor.USER)
        self.thread.add_message("Message 2", actor=Actor.AGENT)
        
        thread_id = self.thread.id
        
        # Delete the thread
        self.thread.delete()
        
        # Verify messages are also deleted
        self.assertEqual(Message.objects.filter(thread_id=thread_id).count(), 0)

    def test_message_str_representation(self):
        """Test the string representation of a Message"""
        message = self.thread.add_message("Test message content", actor=Actor.USER)
        self.assertEqual(str(message), "Test message content")

    def test_add_message_with_all_actor_values(self):
        """Test that all valid Actor values work correctly"""
        # Test with each valid actor
        for actor_value, actor_label in Actor.choices:
            message = self.thread.add_message(
                f"Message from {actor_label}", 
                actor=actor_value
            )
            self.assertEqual(message.actor, actor_value)
            self.assertEqual(message.text, f"Message from {actor_label}")

    def test_thread_user_relationship(self):
        """Test that thread is properly associated with user"""
        self.assertEqual(self.thread.user, self.user)
        
        # Verify through reverse relationship
        user_threads = Thread.objects.filter(user=self.user)
        self.assertIn(self.thread, user_threads)
