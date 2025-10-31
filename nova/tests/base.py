# nova/tests/base.py
from django.test import TestCase
from django.contrib.auth.models import User
from nova.models.UserObjects import UserProfile, UserParameters


class BaseTestCase(TestCase):
    """
    Base class for all tests in the Nova project.
    Provides common setup like a test user with profile and parameters.
    """

    def setUp(self):
        super().setUp()
        # Create a test user with auto-created profile and parameters (via signals)
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        # Ensure profile and parameters are created (signals should handle this)
        self.profile = UserProfile.objects.get(user=self.user)
        self.params = UserParameters.objects.get(user=self.user)

    def tearDown(self):
        # Optional: Clean up if needed, but Django TestCase handles DB rollback
        super().tearDown()
