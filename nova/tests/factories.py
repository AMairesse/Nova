# nova/tests/factories.py
from django.contrib.auth import get_user_model

from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Tool import Tool

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


def create_tool(user, name="Test Tool", tool_type=Tool.ToolType.BUILTIN, tool_subtype="memory"):
    return Tool.objects.create(
        user=user,
        name=name,
        description="A test tool",
        tool_type=tool_type,
        tool_subtype=tool_subtype,
    )
