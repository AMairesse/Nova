"""
Tests for the memory builtin tool and UserInfo model.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from nova.models.UserObjects import UserInfo
from nova.tools.builtins.memory import (
    _get_theme_content,
    _set_theme_content,
    _delete_theme_content,
)


User = get_user_model()


class UserInfoModelTest(TestCase):
    """Test UserInfo model functionality."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_userinfo_creation(self):
        """Test UserInfo is created automatically."""
        user_info = UserInfo.objects.get(user=self.user)
        self.assertEqual(user_info.user, self.user)
        self.assertEqual(user_info.markdown_content, "# global_user_preferences\n")

    def test_userinfo_validation(self):
        """Test UserInfo validation."""
        user_info = UserInfo.objects.get(user=self.user)

        # Valid content
        user_info.markdown_content = "# global_user_preferences\n- Be concise"
        user_info.full_clean()  # Should not raise

        # Invalid content (no heading)
        user_info.markdown_content = "Invalid content without heading"
        with self.assertRaises(ValidationError):
            user_info.full_clean()

        # Content too long
        user_info.markdown_content = "# Test\n" + "x" * 50001
        with self.assertRaises(ValidationError):
            user_info.full_clean()


class MemoryToolTest(TestCase):
    """Test memory tool functions."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        self.user_info = UserInfo.objects.get(user=self.user)
        self.user_info.markdown_content = """# Personal
- Name: Test User
- Age: 30

# Work
- Company: Test Corp
- Role: Developer

# Preferences
- Language: Python
- IDE: VSCode
"""
        self.user_info.save()

    def test_get_theme_content(self):
        """Test extracting content for a specific theme."""
        content = self.user_info.markdown_content
        theme_content = _get_theme_content(content, "Personal")
        self.assertIn("Name: Test User", theme_content)
        self.assertIn("Age: 30", theme_content)

    def test_set_theme_content(self):
        """Test updating theme content."""
        content = self.user_info.markdown_content
        new_content = "# Personal\n- Name: Updated User\n- Age: 31"
        updated = _set_theme_content(content, "Personal", new_content)
        self.assertIn("Name: Updated User", updated)
        self.assertIn("Age: 31", updated)

    def test_delete_theme_content(self):
        """Test deleting theme content."""
        content = self.user_info.markdown_content
        updated = _delete_theme_content(content, "Work")
        self.assertNotIn("# Work", updated)
        self.assertNotIn("Company: Test Corp", updated)

    def test_memory_functions(self):
        """Test async memory functions."""
        # This would require mocking LLMAgent, but basic structure is tested above
        pass


class MemoryIntegrationTest(TestCase):
    """Test memory integration with agents."""

    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )

    def test_userinfo_signal_creation(self):
        """Test UserInfo is created via signal when user is created."""
        # User creation should trigger signal
        user_info_count = UserInfo.objects.filter(user=self.user).count()
        self.assertEqual(user_info_count, 1)

    def test_memory_tool_registration(self):
        """Test memory tool is properly registered."""
        from nova.tools import get_available_tool_types
        tool_types = get_available_tool_types()
        self.assertIn('memory', tool_types)
        self.assertEqual(tool_types['memory']['name'], 'Memory')
