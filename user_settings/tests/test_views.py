# user_settings/tests/test_views.py
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse

User = get_user_model()


class GeneralSettingsViewTest(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='oldpassword123'
        )
        self.client.login(username='testuser', password='oldpassword123')

    def test_get_general_settings(self):
        """Test GET request to general settings returns fragment"""
        response = self.client.get(reverse('user_settings:general') + '?partial=1')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change Password')
        self.assertContains(response, 'old_password')

    def test_password_change_success(self):
        """Test successful password change"""
        data = {
            'old_password': 'oldpassword123',
            'new_password1': 'newpassword456',
            'new_password2': 'newpassword456',
        }
        response = self.client.post(
            reverse('user_settings:general') + '?partial=1',
            data,
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Password changed successfully')

        # Verify password was changed
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpassword456'))

    def test_password_change_wrong_old_password(self):
        """Test password change with wrong old password"""
        data = {
            'old_password': 'wrongpassword',
            'new_password1': 'newpassword456',
            'new_password2': 'newpassword456',
        }
        response = self.client.post(
            reverse('user_settings:general') + '?partial=1',
            data,
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change Password')  # Form re-rendered
        self.assertContains(response, 'error')  # Error message

    def test_password_change_mismatch(self):
        """Test password change with mismatched new passwords"""
        data = {
            'old_password': 'oldpassword123',
            'new_password1': 'newpassword456',
            'new_password2': 'differentpassword',
        }
        response = self.client.post(
            reverse('user_settings:general') + '?partial=1',
            data,
            HTTP_HX_REQUEST='true'
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Change Password')
        self.assertContains(response, 'error')