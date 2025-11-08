# nova/tests/models/test_tool.py
from django.core.exceptions import ValidationError
from django.test import override_settings

from nova.models.Tool import Tool, ToolCredential, check_and_create_searxng_tool, check_and_create_judge0_tool
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
    def test_check_and_create_searxng_tool_simple_create(self):
        """
        Test system SearXNG tool creation function for basic creation of the system tool.
        """
        # Call the function, should create the tool
        check_and_create_searxng_tool()

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
    def test_check_and_create_searxng_tool_create_credentials_only(self):
        """
        Test system SearXNG tool creation function for missing credentials on an already existing tool
        """
        # First create a system tool for searxng but without credential
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='searxng')

        # Call the function, should create the missing credentials
        check_and_create_searxng_tool()

        tool_credentials = ToolCredential.objects.filter(user=None, tool=tool).first()
        self.assertIsNotNone(tool_credentials)
        self.assertEqual(tool_credentials.config['searxng_url'], 'http://searxng:8080')
        self.assertEqual(tool_credentials.config['num_results'], 5)

    @override_settings(
        SEARNGX_SERVER_URL='http://searxng:8080',
        SEARNGX_NUM_RESULTS=5,
    )
    def test_check_and_create_searxng_update_credentials(self):
        """
        Test system SearXNG tool creation function for update of credentials on an already existing tool
        """
        # First create a system tool for searxng and it's credential
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='searxng')
        tool_credentials = ToolCredential.objects.create(user=None, tool=tool,
                                                         config={'searxng_url': 'http:olserver:8000',
                                                                 'num_results': 10})

        # Call the function, should update the credentials
        check_and_create_searxng_tool()

        tool_credentials.refresh_from_db()
        self.assertEqual(tool_credentials.config['searxng_url'], 'http://searxng:8080')
        self.assertEqual(tool_credentials.config['num_results'], 5)

    @override_settings(
        SEARNGX_SERVER_URL=None,
        SEARNGX_NUM_RESULTS=None,
    )
    def test_check_and_create_searxng_delete_unused(self):
        """
        Test system SearXNG tool creation function for deletion of an unused tool
        """
        # First create a system tool for searxng
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='searxng')
        ToolCredential.objects.create(user=None, tool=tool,
                                      config={'searxng_url': 'http://searxng:8080',
                                              'num_results': 5})

        # Call the function, should delete the tool
        check_and_create_searxng_tool()

        self.assertFalse(ToolCredential.objects.filter(user=None, tool=tool).exists())
        self.assertFalse(Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                             tool_subtype='searxng').exists())

    @override_settings(
        SEARNGX_SERVER_URL=None,
        SEARNGX_NUM_RESULTS=None,
    )
    def test_check_and_create_searxng_delete_used(self):
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
        with self.assertLogs("nova.models.Tool") as logger:
            check_and_create_searxng_tool()

        # Check that a warning was created
        self.assertListEqual(logger.output, [
            """WARNING:nova.models.Tool:WARNING: SEARXNG_SERVER_URL not set, but a system
                       tool exists and is being used by at least one agent."""
        ])

        # Check that the tool and credentials still exists
        self.assertTrue(ToolCredential.objects.filter(user=None, tool=tool).exists())
        self.assertTrue(Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                            tool_subtype='searxng').exists())

    @override_settings(
        JUDGE0_SERVER_URL='http://judge0:2358',
    )
    def test_check_and_create_judge0_tool_simple_create(self):
        """
        Test system Judge0 tool creation function for basic creation of the system tool.
        """
        # Call the function, should create the tool
        check_and_create_judge0_tool()

        tool = Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                   tool_subtype='code_execution').first()
        tool_credentials = ToolCredential.objects.filter(user=None, tool=tool).first()
        self.assertIsNotNone(tool)
        self.assertIsNotNone(tool_credentials)
        self.assertEqual(tool.name, 'System - Code Execution')
        self.assertEqual(tool_credentials.config['judge0_url'], 'http://judge0:2358')

    @override_settings(
        JUDGE0_SERVER_URL='http://judge0:2358',
    )
    def test_check_and_create_judge0_tool_create_credentials_only(self):
        """
        Test system Judge0 tool creation function for missing credentials on an already existing tool
        """
        # First create a system tool for judge0 but without credential
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='code_execution')

        # Call the function, should create the missing credentials
        check_and_create_judge0_tool()

        tool_credentials = ToolCredential.objects.filter(user=None, tool=tool).first()
        self.assertIsNotNone(tool_credentials)
        self.assertEqual(tool_credentials.config['judge0_url'], 'http://judge0:2358')

    @override_settings(
        JUDGE0_SERVER_URL='http://judge0:2358',
    )
    def test_check_and_create_judge0_update_credentials(self):
        """
        Test system Judge0 tool creation function for update of credentials on an already existing tool
        """
        # First create a system tool for judge0 and it's credential
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='code_execution')
        tool_credentials = ToolCredential.objects.create(user=None, tool=tool,
                                                         config={'judge0_url': 'http://oldserver:8000'})

        # Call the function, should update the credentials
        check_and_create_judge0_tool()

        tool_credentials.refresh_from_db()
        self.assertEqual(tool_credentials.config['judge0_url'], 'http://judge0:2358')

    @override_settings(
        JUDGE0_SERVER_URL=None,
    )
    def test_check_and_create_judge0_delete_unused(self):
        """
        Test system Judge0 tool creation function for deletion of an unused tool
        """
        # First create a system tool for judge0
        tool = Tool.objects.create(user=None, tool_type=Tool.ToolType.BUILTIN, tool_subtype='code_execution')
        ToolCredential.objects.create(user=None, tool=tool,
                                      config={'judge0_url': 'http://judge0:2358'})

        # Call the function, should delete the tool
        check_and_create_judge0_tool()

        self.assertFalse(ToolCredential.objects.filter(user=None, tool=tool).exists())
        self.assertFalse(Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                             tool_subtype='code_execution').exists())

    @override_settings(
        JUDGE0_SERVER_URL=None,
    )
    def test_check_and_create_judge0_delete_used(self):
        """
        Test system Judge0 tool creation function for deletion of an unused tool
        """
        # First create a system tool for judge0
        tool = create_tool(None, name='System - Code Execution', tool_subtype="code_execution")
        ToolCredential.objects.create(user=None, tool=tool,
                                      config={'judge0_url': 'http://judge0:2358'})

        # Create an agent using the tool
        provider = create_provider(self.user)
        agent = create_agent(self.user, provider)
        agent.tools.add(tool)

        # Call the function, should not delete the tool
        with self.assertLogs("nova.models.Tool") as logger:
            check_and_create_judge0_tool()

        # Check that a warning was created
        self.assertListEqual(logger.output, [
            """WARNING:nova.models.Tool:WARNING: JUDGE0_SERVER_URL not set, but a system
                       tool exists and is being used by at least one agent."""
        ])

        # Check that the tool and credentials still exists
        self.assertTrue(ToolCredential.objects.filter(user=None, tool=tool).exists())
        self.assertTrue(Tool.objects.filter(user=None, tool_type=Tool.ToolType.BUILTIN,
                                            tool_subtype='code_execution').exists())
