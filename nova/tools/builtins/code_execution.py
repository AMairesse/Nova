# nova/tools/builtins/code_execution.py
import aiohttp
import asyncio
import logging
from typing import Dict, List, Optional
from django.utils.translation import gettext_lazy as _
from langchain_core.tools import StructuredTool
from asgiref.sync import sync_to_async

from nova.llm.llm_agent import LLMAgent
from nova.models.models import Tool, ToolCredential

logger = logging.getLogger(__name__)

METADATA = {
    'name': 'Code Execution',
    'description': 'Execute code snippets securely using Judge0 server',
    'requires_config': True,
    'config_fields': [
        {'name': 'judge0_url', 'type': 'string', 'label': _('Judge0 Server URL'), 'required': True},
        {'name': 'api_key', 'type': 'string', 'label': _('Judge0 API Key (optional)'), 'required': False},
        {'name': 'timeout', 'type': 'integer', 'label': _('Default execution timeout (seconds)'),
         'required': False, 'default': 5},
    ],
    'test_function': 'test_judge0_access',
    'test_function_args': ['tool'],
}


async def test_judge0_access(tool: Tool) -> Dict[str, str]:
    """Test connection to Judge0 by fetching supported languages."""
    try:
        host = await get_judge0_host(tool)
        languages = await list_supported_languages(host)
        count = len(languages.split(', ')) if languages else 0
        if count > 0:
            return {
                "status": "success",
                "message": _(f"{count} supported languages found")
            }
        else:
            return {
                "status": "error",
                "message": _("No languages found - check server configuration")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": _("Connection error: {err}").format(err=str(e))
        }


async def get_judge0_host(tool: Tool) -> str:
    """Fetch Judge0 URL from tool credentials."""
    # Manage credentials
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    cred = await sync_to_async(
        lambda: ToolCredential.objects.filter(user=tool_user, tool=tool).first(),
        thread_sensitive=False
    )()
    if not cred:
        raise ValueError(_("No credential configured for this Code Execution tool."))

    host = cred.config.get("judge0_url")
    if not host:
        raise ValueError(_("Field ‘judge0_url’ is missing from the configuration."))

    return host.rstrip('/')


async def get_judge0_config(tool: Tool) -> Dict[str, any]:
    """Fetch full Judge0 config from credentials."""
    tool_user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    cred = await sync_to_async(
        lambda: ToolCredential.objects.filter(user=tool_user, tool=tool).first(),
        thread_sensitive=False
    )()
    if not cred:
        raise ValueError(_("No credential configured for this Code Execution tool."))

    return {
        'url': cred.config.get("judge0_url", '').rstrip('/'),
        'api_key': cred.config.get("api_key"),
        'timeout': int(cred.config.get("timeout", 5)) if cred.config.get("timeout") else 5,
    }


async def api_request(method: str, url: str, headers: Optional[Dict] = None, data: Optional[Dict] = None) -> Dict:
    """Async HTTP request helper for Judge0 API."""
    async with aiohttp.ClientSession() as session:
        kwargs = {'headers': headers or {}, 'json': data} if data else {'headers': headers or {}}
        async with session.request(method, url, **kwargs) as response:
            if response.status != 200 and response.status != 201:
                error_text = await response.text()
                raise ValueError(f"Judge0 API error: {response.status} - {error_text}")
            return await response.json()


async def list_supported_languages(host: str) -> str:
    """List supported languages from Judge0."""
    try:
        response = await api_request('GET', f"{host}/languages")
        languages = [lang['name'] for lang in response]
        return ", ".join(languages)
    except Exception as e:
        logger.error(f"Error listing languages: {str(e)}")
        return _("Error listing languages: {error}").format(error=str(e))


async def get_execution_status(host: str, token: str) -> str:
    """Get status of a submission."""
    try:
        response = await api_request('GET', f"{host}/submissions/{token}?base64_encoded=false")
        status = response.get('status', {}).get('description', 'Unknown')
        stdout = response.get('stdout', '')
        stderr = response.get('stderr', '')
        return f"Status: {status}\nStdout: {stdout}\nStderr: {stderr}"
    except Exception as e:
        logger.error(f"Error getting status: {str(e)}")
        return _("Error getting status: {error}").format(error=str(e))


async def compile_code(host: str, code: str, language: str) -> str:
    """Compile code without execution (for compiled languages)."""
    try:
        data = {
            "source_code": code,
            "language": language,
            "compiler_options": "",
            "command_line_arguments": ""
        }
        response = await api_request('POST', f"{host}/submissions?base64_encoded=false&wait=false", data=data)
        token = response.get('token')

        # Poll for completion
        for _i in range(10):  # Max 10 attempts
            status = await get_execution_status(host, token)
            if "Status: Accepted" in status or "Status: Compilation Error" in status:
                return status
            await asyncio.sleep(1)

        return _("Compilation timeout")
    except Exception as e:
        logger.error(f"Error compiling code: {str(e)}")
        return _("Error compiling code: {error}").format(error=str(e))


async def execute_code(host: str, code: str, language: str, input_data: Optional[str] = None, timeout: int = 5) -> str:
    """Execute code and return output."""
    try:
        data = {
            "source_code": code,
            "language": language,
            "stdin": input_data or "",
            "cpu_time_limit": timeout,
            "memory_limit": 128000  # 128MB default
        }
        response = await api_request('POST', f"{host}/submissions?base64_encoded=false&wait=true", data=data)
        stdout = response.get('stdout', '')
        stderr = response.get('stderr', '')
        status = response.get('status', {}).get('description', 'Unknown')
        return f"Status: {status}\nStdout: {stdout}\nStderr: {stderr}"
    except Exception as e:
        logger.error(f"Error executing code: {str(e)}")
        return _("Error executing code: {error}").format(error=str(e))


async def run_code_with_input(host: str, code: str, language: str, inputs: List[str]) -> str:
    """Run code with multiple inputs."""
    try:
        results = []
        for inp in inputs:
            result = await execute_code(host, code, language, inp)
            results.append(f"Input: {inp}\n{result}")
        return "\n\n".join(results)
    except Exception as e:
        logger.error(f"Error running code with inputs: {str(e)}")
        return _("Error running code with inputs: {error}").format(error=str(e))


async def get_functions(tool: Tool, agent: LLMAgent) -> List[StructuredTool]:
    """Return a list of StructuredTool instances."""
    # Fetch config
    config = await get_judge0_config(tool)
    host = config['url']
    default_timeout = config['timeout']

    return [
        StructuredTool.from_function(
            coroutine=lambda **kwargs: list_supported_languages(host),
            name="list_supported_languages",
            description="List the supported programming languages",
            args_schema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda code, language, input_data=None, timeout=default_timeout,
            **kwargs: execute_code(host, code, language, input_data, timeout),
            name="execute_code",
            description="Execute a code snippet and return output",
            args_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code to execute"},
                    "language": {"type": "string", "description": "Programming language (e.g., python3)"},
                    "input_data": {"type": "string", "description": "Optional stdin input"},
                    "timeout": {"type": "integer", "description": "Max execution time in seconds",
                                "default": default_timeout}
                },
                "required": ["code", "language"]
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda token, **kwargs: get_execution_status(host, token),
            name="get_execution_status",
            description="Get status of a previous execution",
            args_schema={
                "type": "object",
                "properties": {
                    "token": {"type": "string", "description": "Submission token"}
                },
                "required": ["token"]
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda code, language, **kwargs: compile_code(host, code, language),
            name="compile_code",
            description="Compile code without executing (for compiled languages)",
            args_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code to compile"},
                    "language": {"type": "string", "description": "Programming language (e.g., c++)"}
                },
                "required": ["code", "language"]
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda code, language, inputs, **kwargs: run_code_with_input(host, code, language, inputs),
            name="run_code_with_input",
            description="Run code with multiple inputs",
            args_schema={
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "The code to execute"},
                    "language": {"type": "string", "description": "Programming language"},
                    "inputs": {"type": "array", "items": {"type": "string"}, "description": "List of stdin inputs"}
                },
                "required": ["code", "language", "inputs"]
            }
        ),
    ]
