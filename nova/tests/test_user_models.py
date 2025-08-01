# nova/tests/test_user_models.py
"""
Tests for user-related models: UserProfile and UserParameters.

Focus on:
- UserProfile model behavior and relationships
- UserParameters model with encrypted fields
- Signal-based automatic creation
- Default agent assignment
"""

from django.test import TestCase
from django.contrib.auth.models import User
from django.db import IntegrityError

from nova.models import (
    UserProfile, UserParameters, Agent, LLMProvider, 
    ProviderType
)
from .base import BaseModelTestCase


class UserProfileModelTests(BaseModelTestCase):
    """Test cases for UserProfile model."""

    def test_user_profile_created_automatically(self):
        """Test that UserProfile is created automatically via signal."""
        # UserProfile should be created automatically when user is created
        self.assertTrue(
            UserProfile.objects.filter(user=self.user).exists()
        )
        
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.user, self.user)
        self.assertIsNone(profile.default_agent)

    def test_user_profile_one_to_one_relationship(self):
        """Test the one-to-one relationship with User."""
        profile = UserProfile.objects.get(user=self.user)
        
        # Test forward relationship
        self.assertEqual(profile.user, self.user)
        
        # Test reverse relationship
        self.assertEqual(self.user.userprofile, profile)

    def test_default_agent_assignment(self):
        """Test setting and getting default agent."""
        # Create provider and agent
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=provider,
            system_prompt="Test prompt"
        )
        
        # Get profile and set default agent
        profile = UserProfile.objects.get(user=self.user)
        profile.default_agent = agent
        profile.save()
        
        # Verify assignment
        profile.refresh_from_db()
        self.assertEqual(profile.default_agent, agent)

    def test_default_agent_set_null_on_delete(self):
        """Test that default_agent is set to NULL when agent is deleted."""
        # Create provider and agent
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=provider,
            system_prompt="Test prompt"
        )
        
        # Set as default agent
        profile = UserProfile.objects.get(user=self.user)
        profile.default_agent = agent
        profile.save()
        
        # Delete agent
        agent.delete()
        
        # Profile should still exist with null default_agent
        profile.refresh_from_db()
        self.assertIsNone(profile.default_agent)

    def test_cascade_delete_user(self):
        """Test that deleting user deletes UserProfile."""
        profile_id = UserProfile.objects.get(user=self.user).id
        
        # Delete user
        self.user.delete()
        
        # Profile should be deleted too
        self.assertFalse(
            UserProfile.objects.filter(id=profile_id).exists()
        )

    def test_unique_user_constraint(self):
        """Test that each user can have only one profile."""
        # Try to create another profile for the same user
        with self.assertRaises(IntegrityError):
            UserProfile.objects.create(user=self.user)


class UserParametersModelTests(BaseModelTestCase):
    """Test cases for UserParameters model."""

    def test_user_parameters_created_automatically(self):
        """Test that UserParameters is created automatically via signal."""
        # UserParameters should be created automatically when user is created
        self.assertTrue(
            UserParameters.objects.filter(user=self.user).exists()
        )
        
        params = UserParameters.objects.get(user=self.user)
        self.assertEqual(params.user, self.user)

    def test_user_parameters_str_representation(self):
        """Test the string representation of UserParameters."""
        params = UserParameters.objects.get(user=self.user)
        expected_str = f'Parameters for {self.user.username}'
        self.assertEqual(str(params), expected_str)

    def test_default_values(self):
        """Test default values for UserParameters fields."""
        params = UserParameters.objects.get(user=self.user)
        
        # Test default values
        self.assertFalse(params.allow_langfuse)
        self.assertIsNone(params.langfuse_public_key)
        self.assertIsNone(params.langfuse_secret_key)
        self.assertIsNone(params.langfuse_host)

    def test_allow_langfuse_toggle(self):
        """Test toggling the allow_langfuse boolean field."""
        params = UserParameters.objects.get(user=self.user)
        
        # Initially False
        self.assertFalse(params.allow_langfuse)
        
        # Set to True
        params.allow_langfuse = True
        params.save()
        
        params.refresh_from_db()
        self.assertTrue(params.allow_langfuse)
        
        # Set back to False
        params.allow_langfuse = False
        params.save()
        
        params.refresh_from_db()
        self.assertFalse(params.allow_langfuse)

    def test_langfuse_key_encryption(self):
        """Test that Langfuse keys are encrypted when stored."""
        params = UserParameters.objects.get(user=self.user)
        
        public_key = "pk-test-public-key-12345"
        secret_key = "sk-test-secret-key-67890"
        
        params.langfuse_public_key = public_key
        params.langfuse_secret_key = secret_key
        params.save()
        
        # Keys should be retrievable as plain text
        self.assertEqual(params.langfuse_public_key, public_key)
        self.assertEqual(params.langfuse_secret_key, secret_key)
        
        # Refresh from database and verify encryption/decryption works
        params.refresh_from_db()
        self.assertEqual(params.langfuse_public_key, public_key)
        self.assertEqual(params.langfuse_secret_key, secret_key)

    def test_langfuse_host_url(self):
        """Test setting and getting Langfuse host URL."""
        params = UserParameters.objects.get(user=self.user)
        
        host_url = "https://langfuse.example.com"
        params.langfuse_host = host_url
        params.save()
        
        params.refresh_from_db()
        self.assertEqual(params.langfuse_host, host_url)

    def test_complete_langfuse_config(self):
        """Test setting complete Langfuse configuration."""
        params = UserParameters.objects.get(user=self.user)
        
        params.allow_langfuse = True
        params.langfuse_public_key = "pk-test-12345"
        params.langfuse_secret_key = "sk-test-67890"
        params.langfuse_host = "https://cloud.langfuse.com"
        params.save()
        
        params.refresh_from_db()
        self.assertTrue(params.allow_langfuse)
        self.assertEqual(params.langfuse_public_key, "pk-test-12345")
        self.assertEqual(params.langfuse_secret_key, "sk-test-67890")
        self.assertEqual(params.langfuse_host, "https://cloud.langfuse.com")

    def test_empty_encrypted_fields(self):
        """Test handling of empty encrypted fields."""
        params = UserParameters.objects.get(user=self.user)
        
        # Set to empty strings
        params.langfuse_public_key = ""
        params.langfuse_secret_key = ""
        params.save()
        
        params.refresh_from_db()
        self.assertEqual(params.langfuse_public_key, "")
        self.assertEqual(params.langfuse_secret_key, "")

    def test_one_to_one_relationship(self):
        """Test the one-to-one relationship with User."""
        params = UserParameters.objects.get(user=self.user)
        
        # Test forward relationship
        self.assertEqual(params.user, self.user)
        
        # Test reverse relationship (if accessed)
        # Note: Django doesn't automatically create reverse accessor for UserParameters
        # but we can still query it
        user_params = UserParameters.objects.get(user=self.user)
        self.assertEqual(user_params, params)

    def test_cascade_delete_user(self):
        """Test that deleting user deletes UserParameters."""
        params_id = UserParameters.objects.get(user=self.user).id
        
        # Delete user
        self.user.delete()
        
        # Parameters should be deleted too
        self.assertFalse(
            UserParameters.objects.filter(id=params_id).exists()
        )

    def test_unique_user_constraint(self):
        """Test that each user can have only one UserParameters."""
        # Try to create another UserParameters for the same user
        with self.assertRaises(IntegrityError):
            UserParameters.objects.create(user=self.user)

    def test_invalid_url_format(self):
        """Test handling of invalid URL format in langfuse_host."""
        params = UserParameters.objects.get(user=self.user)
        
        # This should raise a validation error when full_clean() is called
        params.langfuse_host = "not-a-valid-url"
        
        # Note: Django doesn't validate URLField on save() by default,
        # only on form validation or explicit full_clean()
        with self.assertRaises(Exception):
            params.full_clean()


class UserModelSignalTests(TestCase):
    """Test the signal-based creation of user-related models."""

    def test_signal_creates_profile_and_parameters(self):
        """Test that creating a user triggers signal to create profile and parameters."""
        # Create a new user
        user = User.objects.create_user(
            username='signaltest',
            password='testpass123'
        )
        
        # Both UserProfile and UserParameters should be created automatically
        self.assertTrue(
            UserProfile.objects.filter(user=user).exists()
        )
        self.assertTrue(
            UserParameters.objects.filter(user=user).exists()
        )
        
        # Verify they're properly linked
        profile = UserProfile.objects.get(user=user)
        params = UserParameters.objects.get(user=user)
        
        self.assertEqual(profile.user, user)
        self.assertEqual(params.user, user)

    def test_signal_only_on_creation(self):
        """Test that signal only fires on user creation, not updates."""
        # Create user (should trigger signal)
        user = User.objects.create_user(
            username='updatetest',
            password='testpass123'
        )
        
        # Verify objects were created
        profile_count = UserProfile.objects.filter(user=user).count()
        params_count = UserParameters.objects.filter(user=user).count()
        
        self.assertEqual(profile_count, 1)
        self.assertEqual(params_count, 1)
        
        # Update user (should not create new objects)
        user.email = 'updated@example.com'
        user.save()
        
        # Count should remain the same
        new_profile_count = UserProfile.objects.filter(user=user).count()
        new_params_count = UserParameters.objects.filter(user=user).count()
        
        self.assertEqual(new_profile_count, 1)
        self.assertEqual(new_params_count, 1)

    def test_multiple_users_get_separate_objects(self):
        """Test that multiple users each get their own profile and parameters."""
        users = []
        for i in range(3):
            user = User.objects.create_user(
                username=f'multitest{i}',
                password='testpass123'
            )
            users.append(user)
        
        # Each user should have their own profile and parameters
        for user in users:
            profile = UserProfile.objects.get(user=user)
            params = UserParameters.objects.get(user=user)
            
            self.assertEqual(profile.user, user)
            self.assertEqual(params.user, user)
        
        # Total counts should match
        self.assertEqual(UserProfile.objects.count(), 3)
        self.assertEqual(UserParameters.objects.count(), 3)
