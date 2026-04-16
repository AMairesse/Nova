# nova/tests/models/test_tool.py
from django.core.exceptions import ValidationError
from django.test import override_settings

from nova.models.Tool import Tool, ToolCredential
from nova.plugins.catalog import sync_python_system_backend, sync_search_system_backend
from nova.tests.base import BaseTestCase
from nova.tests.factories import create_provider, create_agent, create_tool


class ToolModelsTest(BaseTestCase):
    def test_tool_creation_builtin(self):
        """
        Test Tool model creation for builtin tools.
        Ensures that builtin tools are created with required subtype.
        """
        tool = Tool.objects.create(
            user=self.user,
            name="Test Tool",
            description="A test tool",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
        )
        self.assertEqual(tool.user, self.user)
        self.assertEqual(tool.name, "Test Tool")
        self.assertEqual(tool.tool_type, Tool.ToolType.BUILTIN)

    def test_tool_creation_api(self):
        """
        Test Tool model creation for API tools.
        Ensures that API tools are created with required endpoint.
        """
        tool = Tool.objects.create(
            user=self.user,
            name="API Tool",
            description="An API tool",
            tool_type=Tool.ToolType.API,
            endpoint="https://api.example.com",
        )
        self.assertEqual(tool.tool_type, Tool.ToolType.API)
        self.assertEqual(tool.endpoint, "https://api.example.com")

    def test_tool_clean_builtin_valid(self):
        """
        Test Tool validation for valid builtin tools.
        Verifies that builtin tools with subtypes pass validation.
        """
        tool = Tool(
            user=self.user,
            name="Test",
            description="Test",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
        )
        tool.full_clean()  # Should not raise

    def test_tool_clean_builtin_missing_subtype(self):
        """
        Test Tool validation for builtin tools without subtype.
        Ensures that builtin tools require a subtype.
        """
        tool = Tool(
            user=self.user,
            name="Test",
            description="Test",
            tool_type=Tool.ToolType.BUILTIN,
        )
        with self.assertRaises(ValidationError):
            tool.full_clean()

    def test_tool_creation_builtin_invalid_subtype(self):
        """
        Test Tool model creation for an invalid subtype
        """
        tool = Tool(
                user=self.user,
                name="Test Tool",
                description="A test tool",
                tool_type=Tool.ToolType.BUILTIN,
                tool_subtype="does_not_exist"
        )
        with self.assertRaises(ValidationError):
            tool.full_clean()

    def test_tool_clean_api_missing_endpoint(self):
        """
        Test Tool validation for API tools without endpoint.
        Ensures that API tools require an endpoint.
        """
        tool = Tool(
            user=self.user,
            name="Test",
            description="Test",
            tool_type=Tool.ToolType.API,
        )
        with self.assertRaises(ValidationError):
            tool.full_clean()

    def test_tool_str(self):
        """
        Test Tool string representation.
        Verifies that __str__ returns tool name and type.
        """
        tool = Tool.objects.create(
            user=self.user,
            name="Test Tool",
            description="Test",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="memory",
        )
        self.assertEqual(str(tool), "Test Tool (builtin)")

    def test_tool_credential_creation(self):
        """
        Test ToolCredential model creation.
        Ensures that credentials are properly linked to tools and users.
        """
        tool = create_tool(self.user)
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            auth_type="basic",
            username="test",
            password="secret",
        )
        self.assertEqual(cred.user, self.user)
        self.assertEqual(cred.tool, tool)
        self.assertEqual(cred.auth_type, "basic")

    def test_tool_credential_str(self):
        """
        Test ToolCredential string representation.
        Verifies that __str__ includes username and tool name.
        """
        tool = create_tool(self.user)
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=tool,
            auth_type="basic",
        )
        self.assertIn("testuser's credentials for", str(cred))

    @override_settings(
        SEARNGX_SERVER_URL='http://searxng:8080',
        SEARNGX_NUM_RESULTS=5,
    )
    def test_sync_search_system_backend_simple_create(self):
        """
        Test system SearXNG tool creation function for basic creation of the system tool.
        """
        # Call the function, should create the tool
        sync_search_system_backend()

        tool = Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                   tool_subtype='searxng').first()
        tool_credentials = ToolCredential.objects.filter(user=None, tool=tool).first()
        self.assertIsNotNone(tool)
        self.assertIsNotNone(tool_credentials)
        self.assertEqual(tool.name, 'System - SearXNG')
        self.assertEqual(tool_credentials.config['searxng_url'], 'http://searxng:8080')
        self.assertEqual(tool_credentials.config['num_results'], 5)

    @override_settings(
        SEARNGX_SERVER_URL='http://searxng:8080',
        SEARNGX_NUM_RESULTS=5,
    )
    def test_sync_search_system_backend_create_credentials_only(self):
        """
        Test system SearXNG tool creation function for missing credentials on an already existing tool
        """
        # First create a system tool for searxng but without credential
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='searxng')

        # Call the function, should create the missing credentials
        sync_search_system_backend()

        tool_credentials = ToolCredential.objects.filter(user=None, tool=tool).first()
        self.assertIsNotNone(tool_credentials)
        self.assertEqual(tool_credentials.config['searxng_url'], 'http://searxng:8080')
        self.assertEqual(tool_credentials.config['num_results'], 5)

    @override_settings(
        SEARNGX_SERVER_URL='http://searxng:8080',
        SEARNGX_NUM_RESULTS=5,
    )
    def test_sync_search_system_backend_updates_credentials(self):
        """
        Test system SearXNG tool creation function for update of credentials on an already existing tool
        """
        # First create a system tool for searxng and it's credential
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='searxng')
        tool_credentials = ToolCredential.objects.create(user=None, tool=tool,
                                                         config={'searxng_url': 'http:olserver:8000',
                                                                 'num_results': 10})

        # Call the function, should update the credentials
        sync_search_system_backend()

        tool_credentials.refresh_from_db()
        self.assertEqual(tool_credentials.config['searxng_url'], 'http://searxng:8080')
        self.assertEqual(tool_credentials.config['num_results'], 5)

    @override_settings(
        SEARNGX_SERVER_URL=None,
        SEARNGX_NUM_RESULTS=None,
    )
    def test_sync_search_system_backend_deletes_unused_tool(self):
        """
        Test system SearXNG tool creation function for deletion of an unused tool
        """
        # First create a system tool for searxng
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='searxng')
        ToolCredential.objects.create(user=None, tool=tool,
                                      config={'searxng_url': 'http://searxng:8080',
                                              'num_results': 5})

        # Call the function, should delete the tool
        sync_search_system_backend()

        self.assertFalse(ToolCredential.objects.filter(user=None, tool=tool).exists())
        self.assertFalse(Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                             tool_subtype='searxng').exists())

    @override_settings(
        SEARNGX_SERVER_URL=None,
        SEARNGX_NUM_RESULTS=None,
    )
    def test_sync_search_system_backend_keeps_used_tool_when_config_missing(self):
        """
        Test system SearXNG tool creation function for deletion of an unused tool
        """
        # First create a system tool for searxng
        tool = create_tool(None, name='System - SearXNG', tool_subtype="searxng")
        ToolCredential.objects.create(user=None, tool=tool,
                                      config={'searxng_url': 'http://searxng:8080',
                                              'num_results': 5})

        # Create an agent using the tool
        provider = create_provider(self.user)
        agent = create_agent(self.user, provider)
        agent.tools.add(tool)

        # Call the function, should not delete the tool
        with self.assertLogs("nova.plugins.catalog") as logger:
            sync_search_system_backend()

        # Check that a warning was created
        self.assertListEqual(logger.output, [
            """WARNING:nova.plugins.catalog:WARNING: SEARXNG_SERVER_URL not set, but a system
                       tool exists and is being used by at least one agent."""
        ])

        # Check that the tool and credentials still exists
        self.assertTrue(ToolCredential.objects.filter(user=None, tool=tool).exists())
        self.assertTrue(Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                            tool_subtype='searxng').exists())

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    def test_sync_python_system_backend_simple_create(self):
        """
        Test system Python capability creation for the local exec runner backend.
        """
        sync_python_system_backend()

        tool = Tool.objects.filter(
            user=None,
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype='code_execution',
        ).first()
        self.assertIsNotNone(tool)
        self.assertEqual(tool.name, 'System - Python')
        self.assertFalse(ToolCredential.objects.filter(user=None, tool=tool).exists())

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="http://exec-runner:8080",
        EXEC_RUNNER_SHARED_TOKEN="runner-token",
    )
    def test_sync_python_system_backend_reuses_existing_system_tool(self):
        """
        Test that the system Python capability is reused instead of duplicated.
        """
        tool = Tool.objects.create(
            user=None,
            name='System - Python',
            description='Existing Python capability',
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype='code_execution',
        )

        sync_python_system_backend()

        self.assertEqual(
            Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='code_execution').count(),
            1,
        )
        self.assertTrue(Tool.objects.filter(pk=tool.pk).exists())
        self.assertFalse(ToolCredential.objects.filter(user=None, tool=tool).exists())

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="",
        EXEC_RUNNER_SHARED_TOKEN="",
    )
    def test_sync_python_system_backend_deletes_unused_when_runner_not_configured(self):
        """
        Test that the system Python capability is removed when the runner is not configured and unused.
        """
        tool = Tool.objects.create(
            user=None,
            name='System - Python',
            description='Local Python capability',
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype='code_execution',
        )

        sync_python_system_backend()

        self.assertFalse(Tool.objects.filter(pk=tool.pk).exists())

    @override_settings(
        EXEC_RUNNER_ENABLED=True,
        EXEC_RUNNER_BASE_URL="",
        EXEC_RUNNER_SHARED_TOKEN="",
    )
    def test_sync_python_system_backend_keeps_used_when_runner_not_configured(self):
        """
        Test that the system Python capability is kept when the runner is not configured but already in use.
        """
        tool = create_tool(None, name='System - Python', tool_subtype="code_execution")

        provider = create_provider(self.user)
        agent = create_agent(self.user, provider)
        agent.tools.add(tool)

        with self.assertLogs("nova.plugins.catalog") as logger:
            sync_python_system_backend()

        self.assertListEqual(logger.output, [
            """WARNING:nova.plugins.catalog:WARNING: exec-runner is not configured, but a system
                       tool exists and is being used by at least one agent."""
        ])
        self.assertTrue(Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                            tool_subtype='code_execution').exists())
