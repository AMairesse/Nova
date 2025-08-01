# nova/tests/test_llm_agent.py
"""
Unit tests for LLMAgent, focusing on the refactored create_llm_agent method.
External LangChain dependencies are mocked.
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError

from nova.models import LLMProvider, ProviderType, Agent, Tool, ToolCredential
from nova.llm_agent import LLMAgent


class LLMAgentCreationTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("testuser", password="testpass")

        # Dummy agent with valid provider for initial setup (uses OLLAMA to avoid conflicts)
        self.dummy_provider = LLMProvider.objects.create(
            user=self.user,
            name="Dummy Provider",
            provider_type=ProviderType.OLLAMA,
            model="llama2",
            base_url="http://localhost:11434",
        )
        self.dummy_agent = Agent.objects.create(
            user=self.user,
            name="Dummy Agent",
            system_prompt="Test prompt",
            llm_provider=self.dummy_provider,
        )

    def _create_llm_agent_instance(self, agent=None):
        """Helper to create LLMAgent instance without calling create_llm_agent."""
        agent = agent or self.dummy_agent
        with patch.object(LLMAgent, 'create_llm_agent', return_value=MagicMock()) as mock_create:
            instance = LLMAgent(self.user, thread_id=1, agent=agent)
            mock_create.assert_called_once()
        return instance

    @patch("nova.llm_agent.ChatMistralAI")
    @patch("nova.llm_agent.ChatOpenAI")
    @patch("nova.llm_agent.ChatOllama")
    def test_create_llm_agent_mistral(self, mock_ollama, mock_openai, mock_mistral):
        """Test successful creation for Mistral provider."""
        # Configure mock to return a separate instance mock to avoid __hash__ recording
        mock_mistral.return_value = MagicMock()

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Mistral Provider",
            provider_type=ProviderType.MISTRAL,
            model="mistral-small",
            api_key="test_key",
        )
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            system_prompt="Test prompt",
            llm_provider=provider,
        )

        llm_agent = self._create_llm_agent_instance(agent)
        llm = llm_agent.create_llm_agent()

        mock_mistral.assert_called_once_with(
            model="mistral-small",
            mistral_api_key="test_key",
            temperature=0,
            max_retries=2,
            streaming=True  # Updated for streaming
        )
        self.assertIsNotNone(llm)

    @patch("nova.llm_agent.ChatMistralAI")
    @patch("nova.llm_agent.ChatOpenAI")
    @patch("nova.llm_agent.ChatOllama")
    def test_create_llm_agent_openai(self, mock_ollama, mock_openai, mock_mistral):
        """Test successful creation for OpenAI provider."""
        mock_openai.return_value = MagicMock()

        provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo",
            api_key="test_key",
            base_url="https://api.openai.com/v1",
        )
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            system_prompt="Test prompt",
            llm_provider=provider,
        )

        llm_agent = self._create_llm_agent_instance(agent)
        llm = llm_agent.create_llm_agent()

        mock_openai.assert_called_once_with(
            model="gpt-3.5-turbo",
            openai_api_key="test_key",
            base_url="https://api.openai.com/v1",
            temperature=0,
            max_retries=2,
            streaming=True  # Updated for streaming
        )
        self.assertIsNotNone(llm)

    @patch("nova.llm_agent.ChatMistralAI")
    @patch("nova.llm_agent.ChatOpenAI")
    @patch("nova.llm_agent.ChatOllama")
    def test_create_llm_agent_ollama(self, mock_ollama, mock_openai, mock_mistral):
        """Test successful creation for Ollama provider."""
        mock_ollama.return_value = MagicMock()

        provider = LLMProvider.objects.create(
            user=self.user,
            name="Ollama Provider",
            provider_type=ProviderType.OLLAMA,
            model="llama2",
            base_url="http://localhost:11434",
        )
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            system_prompt="Test prompt",
            llm_provider=provider,
        )

        llm_agent = self._create_llm_agent_instance(agent)
        llm = llm_agent.create_llm_agent()

        mock_ollama.assert_called_once_with(
            model="llama2",
            base_url="http://localhost:11434",
            temperature=0,
            max_retries=2,
            streaming=True  # Updated for streaming
        )
        self.assertIsNotNone(llm)

    @patch("nova.llm_agent.ChatMistralAI")
    @patch("nova.llm_agent.ChatOpenAI")
    @patch("nova.llm_agent.ChatOllama")
    def test_create_llm_agent_lmstudio(self, mock_ollama, mock_openai, mock_mistral):
        """Test successful creation for LMStudio provider."""
        mock_openai.return_value = MagicMock()

        provider = LLMProvider.objects.create(
            user=self.user,
            name="LMStudio Provider",
            provider_type=ProviderType.LLMSTUDIO,
            model="phi2",
            base_url="http://localhost:1234/v1",
        )
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            system_prompt="Test prompt",
            llm_provider=provider,
        )

        llm_agent = self._create_llm_agent_instance(agent)
        llm = llm_agent.create_llm_agent()

        mock_openai.assert_called_once_with(
            model="phi2",
            openai_api_key="None",
            base_url="http://localhost:1234/v1",
            temperature=0,
            max_retries=2,
            streaming=True  # Updated for streaming
        )
        self.assertIsNotNone(llm)

    def test_create_llm_agent_unsupported_provider(self):
        """Test ValueError for unsupported provider type."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Invalid Provider",
            provider_type="invalid_type",  # Not in ProviderType, but for test
            model="invalid",
        )
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            system_prompt="Test prompt",
            llm_provider=provider,
        )

        # Create with dummy valid agent to pass __init__
        llm_agent = self._create_llm_agent_instance(agent)

        with self.assertRaises(ValueError) as ctx:
            llm_agent.create_llm_agent()
        self.assertIn("Unsupported provider type", str(ctx.exception))

    @patch("langfuse.Langfuse")
    @patch("langfuse.langchain.CallbackHandler")
    def test_callbacks_langfuse(self, mock_handler, mock_langfuse):
        """Test Langfuse callbacks if enabled."""
        # Mock the auth_check method
        mock_langfuse_instance = MagicMock()
        mock_langfuse.return_value = mock_langfuse_instance
        mock_langfuse_instance.auth_check.return_value = True
        
        # Mock the handler
        mock_handler_instance = MagicMock()
        mock_handler.return_value = mock_handler_instance
        
        # Create UserParameters with Langfuse enabled directly on the user
        from nova.models import UserParameters
        user_params, created = UserParameters.objects.get_or_create(
            user=self.user,
            defaults={
                'allow_langfuse': True,
                'langfuse_public_key': "pk-test",
                'langfuse_secret_key': "sk-test",
                'langfuse_host': "https://langfuse.example.com"
            }
        )
        if not created:
            user_params.allow_langfuse = True
            user_params.langfuse_public_key = "pk-test"
            user_params.langfuse_secret_key = "sk-test"
            user_params.langfuse_host = "https://langfuse.example.com"
            user_params.save()

        # Refresh the user instance to ensure the relationship is loaded
        self.user.refresh_from_db()

        llm_agent = self._create_llm_agent_instance()
        self.assertIn("callbacks", llm_agent.config)
        mock_langfuse.assert_called_once_with(
            public_key="pk-test",
            secret_key="sk-test",
            host="https://langfuse.example.com"
        )


class LLMAgentLoadToolsTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("testuser", password="testpass")

        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="OpenAI Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo",
            api_key="test_key",
            base_url="https://api.openai.com/v1",
        )

        self.agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            system_prompt="Test prompt",
            llm_provider=self.provider,
        )

        # Mock the create_llm_agent to avoid real LLM creation in setUp
        with patch.object(LLMAgent, 'create_llm_agent', return_value=MagicMock()):
            self.llm_agent = LLMAgent(self.user, thread_id=1, agent=self.agent)

    @patch("nova.llm_agent.StructuredTool")
    @patch("nova.tools.import_module")
    def test_load_builtin_tools(self, mock_import_module, mock_structured_tool):
        """Test loading of builtin tools creates StructuredTools."""
        # Create builtin tool
        builtin_tool = Tool.objects.create(
            user=self.user,
            name="Builtin Tool",
            description="Test builtin",
            tool_type=Tool.ToolType.BUILTIN,
            is_active=True,
            python_path="nova.tools.builtins.test"
        )

        # Associate with agent
        self.agent.tools.add(builtin_tool)

        # Mock import_module and get_functions
        mock_module = MagicMock()
        mock_module.get_functions.return_value = {
            "test_func": {
                "callable": lambda user, tool_id: "result",
                "description": "Test",
                "input_schema": {}
            }
        }
        mock_import_module.return_value = mock_module

        # Mock StructuredTool
        mock_structured_tool.from_function.return_value = MagicMock()

        tools = self.llm_agent._load_agent_tools()

        self.assertEqual(len(tools), 1)
        # Check that our specific module was imported
        mock_import_module.assert_called_with(builtin_tool.python_path)
        # Check that StructuredTool.from_function was called (once per function in the module)
        mock_structured_tool.from_function.assert_called_once()

    @patch("nova.llm_agent.StructuredTool")
    @patch("nova.mcp.client.MCPClient")
    def test_load_mcp_tools(self, mock_mcp_client, mock_structured_tool):
        """Test loading of MCP tools with client and metadata."""
        # Create MCP tool
        mcp_tool = Tool.objects.create(
            user=self.user,
            name="MCP Tool",
            description="Test MCP",
            tool_type=Tool.ToolType.MCP,
            endpoint="https://mcp.example.com",
            is_active=True,
        )

        # Create credential
        ToolCredential.objects.create(
            user=self.user,
            tool=mcp_tool,
            auth_type="token",
            token="test_token",
        )

        # Associate with agent
        self.agent.tools.add(mcp_tool)

        # Mock MCPClient
        mock_client_instance = mock_mcp_client.return_value
        mock_client_instance.list_tools.return_value = [
            {
                "name": "test_func",
                "description": "Test function",
                "input_schema": {"type": "object"},
            }
        ]

        # Mock the StructuredTool
        mock_structured_tool.from_function.return_value = MagicMock()

        tools = self.llm_agent._load_agent_tools()

        self.assertEqual(len(tools), 1)
        mock_mcp_client.assert_called_once_with(
            mcp_tool.endpoint,
            mcp_tool.credentials.first()
        )
        mock_client_instance.list_tools.assert_called_once_with(user_id=self.user.id)
        mock_structured_tool.from_function.assert_called_once()

    @patch("nova.tools.agent_tool_wrapper.AgentToolWrapper")
    def test_load_agent_tools(self, mock_wrapper):
        """Test loading of agents as tools."""
        # Create agent tool
        agent_tool = Agent.objects.create(
            user=self.user,
            name="Tool Agent",
            llm_provider=self.provider,
            system_prompt="Tool prompt",
            is_tool=True,
            tool_description="Tool desc"
        )

        # Associate as agent_tool
        self.agent.agent_tools.add(agent_tool)

        # Mock wrapper
        mock_wrapper_instance = mock_wrapper.return_value
        mock_wrapper_instance.create_langchain_tool.return_value = MagicMock()

        tools = self.llm_agent._load_agent_tools()

        self.assertEqual(len(tools), 1)
        mock_wrapper.assert_called_once_with(
            agent_tool,
            self.user,
            parent_config=self.llm_agent._parent_config
        )
        mock_wrapper_instance.create_langchain_tool.assert_called_once()

    def test_load_agent_tools_cycle_detection(self):
        """Test cycle in agent_tools raises ValidationError on clean."""
        agent1 = Agent.objects.create(
            user=self.user,
            name="Agent1",
            llm_provider=self.provider,
            is_tool=True,
            tool_description="Desc1"
        )
        agent2 = Agent.objects.create(
            user=self.user,
            name="Agent2",
            llm_provider=self.provider,
            is_tool=True,
            tool_description="Desc2"
        )

        # Create cycle
        agent1.agent_tools.add(agent2)
        agent2.agent_tools.add(agent1)

        self.agent.agent_tools.add(agent1)

        with self.assertRaises(ValidationError):
            self.agent.clean()  # Cycle detected in model clean

    @patch("nova.llm_agent.extract_final_answer")
    def test_invoke_with_silent_mode(self, mock_extract):
        """Test invoke in silent_mode uses silent_config."""
        # Mock the agent to avoid actual LangChain invocation
        mock_agent = MagicMock()
        mock_response_message = MagicMock()
        mock_response_message.content = "answer"
        mock_agent.invoke.return_value = {"messages": [mock_response_message]}
        
        # Mock extract_final_answer to return the expected result
        mock_extract.return_value = "answer"
        
        # Replace the agent with our mock
        self.llm_agent.agent = mock_agent

        result = self.llm_agent.invoke("Test question", silent_mode=True)

        # Verify the call was made with the correct config
        mock_agent.invoke.assert_called_once()
        call_args = mock_agent.invoke.call_args
        self.assertEqual(call_args[1]['config'], self.llm_agent.silent_config)
        self.assertEqual(result, "answer")
