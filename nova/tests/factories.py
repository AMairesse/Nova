# nova/tests/factories.py
from django.contrib.auth import get_user_model

from nova.models.APIToolOperation import APIToolOperation
from nova.models.AgentConfig import AgentConfig
from nova.models.Provider import LLMProvider, ProviderType
from nova.models.Tool import Tool, ToolCredential
from nova.plugins.builtins import get_tool_type

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
                 is_tool=False, tool_description="", default_response_mode="text"):
    return AgentConfig.objects.create(
        user=user,
        name=name,
        llm_provider=provider,
        system_prompt=system_prompt,
        is_tool=is_tool,
        tool_description=tool_description,
        default_response_mode=default_response_mode,
    )


def create_tool(user, name="Test Tool", tool_type=Tool.ToolType.BUILTIN, description="Test tool",
                endpoint="https://api.example.com/v1", tool_subtype="memory", is_active=True,
                python_path="", transport_type="") -> Tool:
    builtin_python_path = ""
    if tool_type == Tool.ToolType.BUILTIN and tool_subtype:
        builtin_python_path = str((get_tool_type(tool_subtype) or {}).get("python_path") or "")
    return Tool.objects.create(
        user=user,
        name=name,
        description=description,
        tool_type=tool_type,
        endpoint=endpoint if tool_type in {Tool.ToolType.API, Tool.ToolType.MCP} else None,
        tool_subtype=tool_subtype,
        python_path=(python_path or builtin_python_path),
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


def create_api_tool_operation(
    tool: Tool,
    *,
    name="Get status",
    slug="get-status",
    http_method=APIToolOperation.HTTPMethod.GET,
    path_template="/status",
    query_parameters=None,
    body_parameter="",
    input_schema=None,
    output_schema=None,
    description="",
    is_active=True,
):
    return APIToolOperation.objects.create(
        tool=tool,
        name=name,
        slug=slug,
        description=description,
        http_method=http_method,
        path_template=path_template,
        query_parameters=list(query_parameters or []),
        body_parameter=body_parameter,
        input_schema=input_schema if input_schema is not None else {},
        output_schema=output_schema if output_schema is not None else {},
        is_active=is_active,
    )
