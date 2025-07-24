from django.urls import reverse
from django.test import TestCase, Client
from django.http import JsonResponse
from django.contrib.auth.models import User
from unittest.mock import Mock, patch
from json import loads
from ..models import Thread, Message

class IndexViewTest(TestCase):
    def setUp(self):
        """ Set up test environment for the index view. """
        self.client = Client()
        self.user = User.objects.create(username="test", password="test")
        self.client.force_login(self.user)

    def test_index_renders_threads(self):
        """
        Ensure the index view renders the existing threads in the template.
        """
        Thread.objects.create(subject="First Subject", user=self.user)
        Thread.objects.create(subject="Second Subject", user=self.user)

        response = self.client.get(reverse("index"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "First Subject")
        self.assertContains(response, "Second Subject")

    def test_creating_new_thread(self):
        """
        Ensure a POST request to the create_thread view creates a new thread.
        """
        response = self.client.post(reverse("create_thread"))
        self.assertEqual(response.status_code, 200)
        # Verify that a new thread was created
        self.assertTrue(Thread.objects.all().exists())
        # Verify that the response contains the threadHtml and the thread_id keys
        self.assertContains(response, "threadHtml")
        self.assertContains(response, "thread_id")

    def test_deleting_thread(self):
        """
        Ensure a POST request to the delete_thread view deletes a thread and
        redirects back.
        """
        thread = Thread.objects.create(subject="Test Thread", user=self.user)
        response = self.client.post(reverse("delete_thread", args=[thread.id]))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Thread.objects.filter(id=thread.id).exists())

class AddMessageViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create(username="test", password="test")
        self.client.force_login(self.user)

    def test_add_message_existing_thread(self):
        # Create a thread
        thread = Thread.objects.create(subject="Subject for Messages", user=self.user)

        post_data = {
            "thread_id": thread.id,
            "new_message": "Test message content"
        }
        response = self.client.post(
            reverse("add_message"),
            post_data,
        )
        # Assert response status code is 200
        self.assertEqual(response.status_code, 200)
        
        # Assert JSON response is correct
        self.assertEqual(loads(response.content), {"status": "OK", "threadHtml": None, "thread_id": thread.id})
        
        # Verify the message was actually added to the thread
        updated_thread = Thread.objects.get(id=thread.id)
        # Assuming you have a method to get the latest message
        self.assertIn('Test message content', updated_thread.get_messages().last().text)

    def test_add_message_invalid_thread(self):
        # Test with non-existent thread_id
        data = {
            'thread_id': 99999,  # Non-existent ID
            'new_message': 'Test message'
        }
        
        # Assert that the view raises Thread.DoesNotExist
        with self.assertRaises(Thread.DoesNotExist):
            self.client.post(reverse('add_message'), data=data)
    
    def test_add_message_no_thread_id(self):
        # Test with no thread_id
        data = {
            'new_message': 'Test message'
        }
        
        # Assert that the view returns a 400 status code
        response = self.client.post(reverse('add_message'), data=data)
        self.assertEqual(response.status_code, 200)
        
        # Assert a new thread is created
        self.assertTrue(Thread.objects.filter(user=self.user).exists())
        thread = Thread.objects.filter(user=self.user).first()

        # Assert JSON response is correct
        json_response = loads(response.content)
        self.assertEqual(json_response["status"], "OK")
        self.assertEqual(json_response["thread_id"], thread.id)
        self.assertTrue(json_response["threadHtml"])
