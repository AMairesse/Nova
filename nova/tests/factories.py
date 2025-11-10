# nova/tests/factories.py
from django.contrib.auth import get_user_model

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Tool import Tool, ToolCredential

User = get_user_model()


def create_user(username="testuser", email="test@example.com", password="testpass123"):
    return User.objects.create_user(
        username=username,
        email=email,
        password=password,
    )


def create_provider(user, provider_type=ProviderType.OLLAMA, name="Test Provider", model="test-model"):
    return LLMProvider.objects.create(
        user=user,
        name=name,
        provider_type=provider_type,
        model=model,
        max_context_tokens=4096,
    )


def create_agent(user, provider, name="Test Agent",
                 system_prompt="You are a helpful assistant.",
                 is_tool=False, tool_description=""):
    return AgentConfig.objects.create(
        user=user,
        name=name,
        llm_provider=provider,
        system_prompt=system_prompt,
        is_tool=is_tool,
        tool_description=tool_description,
    )


def create_tool(user, name="Test Tool", tool_type=Tool.ToolType.BUILTIN, description="Test tool",
                endpoint="https://api.example.com/v1", tool_subtype="memory", is_active=True,
                python_path="", transport_type="") -> Tool:
    return Tool.objects.create(
        user=user,
        name=name,
        description=description,
        tool_type=tool_type,
        endpoint=endpoint if tool_type in {Tool.ToolType.API, Tool.ToolType.MCP} else None,
        tool_subtype=tool_subtype,
        is_active=is_active,
        python_path=(
            python_path
            or (
                f"nova.tools.builtins.{tool_subtype}"
                if tool_type == Tool.ToolType.BUILTIN and tool_subtype
                else ""
            )
        ),
        transport_type=transport_type,
    )


def create_tool_credential(user, tool: Tool, auth_type="basic", username=None,
                           password=None, token=None, config=None):
    return ToolCredential.objects.create(
        user=user,
        tool=tool,
        auth_type=auth_type,
        username=username,
        password=password,
        token=token,
        config=config or {},
    )
