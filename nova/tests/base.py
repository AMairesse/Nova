# nova/tests/base.py
"""
Base test classes for Nova test suite.

Provides common setup and utilities for different types of tests.
"""

from django.test import TestCase, Client
from django.contrib.auth.models import User
from nova.models import (
    LLMProvider, Agent, Tool, UserProfile, UserParameters,
    ProviderType, ToolCredential
)


class BaseTestCase(TestCase):
    """Base test case with common setup for all tests."""
    
    def setUp(self):
        """Common setup for all tests."""
        super().setUp()
        # Override in subclasses if needed
    
    def tearDown(self):
        """Common cleanup for all tests."""
        super().tearDown()


class BaseModelTestCase(BaseTestCase):
    """Base test case for model tests with user setup."""
    
    def setUp(self):
        """Set up a test user for model tests."""
        super().setUp()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            email='test@example.com'
        )
        # UserProfile and UserParameters are created automatically via signals


class BaseViewTestCase(BaseTestCase):
    """Base test case for view tests with authenticated client."""
    
    def setUp(self):
        """Set up authenticated client for view tests."""
        super().setUp()
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123',
            email='test@example.com'
        )
        self.client.force_login(self.user)
    
    def tearDown(self):
        """Clean up client session."""
        self.client.logout()
        super().tearDown()


class BaseAPITestCase(BaseViewTestCase):
    """Base test case for API tests with JSON content type."""
    
    def setUp(self):
        """Set up API client with JSON content type."""
        super().setUp()
        self.content_type = 'application/json'
    
    def api_post(self, url, data=None, **extra):
        """Helper method for API POST requests."""
        return self.client.post(
            url, 
            data=data, 
            content_type=self.content_type,
            **extra
        )
    
    def api_get(self, url, data=None, **extra):
        """Helper method for API GET requests."""
        return self.client.get(url, data=data, **extra)


class BaseAgentTestCase(BaseModelTestCase):
    """Base test case for tests that need Agent setup."""
    
    def setUp(self):
        """Set up LLM provider and agent for tests."""
        super().setUp()
        
        # Create LLM provider
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo",
            api_key="test-key"
        )
        
        # Create basic agent
        self.agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=self.provider,
            system_prompt="You are a helpful assistant."
        )


class BaseToolTestCase(BaseModelTestCase):
    """Base test case for tests that need Tool setup."""
    
    def setUp(self):
        """Set up basic tool for tests."""
        super().setUp()
        
        # Create basic tool
        self.tool = Tool.objects.create(
            user=self.user,
            name="Test Tool",
            description="A test tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav"
        )
        
        # Create tool credential
        self.credential = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="basic",
            username="testuser",
            password="testpass",
            config={"caldav_url": "https://example.com/caldav/"}
        )


class BaseIntegrationTestCase(BaseAgentTestCase, BaseToolTestCase):
    """Base test case for integration tests with full setup."""
    
    def setUp(self):
        """Set up complete environment for integration tests."""
        # Call both parent setups
        BaseAgentTestCase.setUp(self)
        BaseToolTestCase.setUp(self)
        
        # Add tool to agent
        self.agent.tools.add(self.tool)
