# nova/plugins/python/service.py
import aiohttp
import asyncio
import logging
import base64
from typing import Any, Dict, List, Optional, Union
from django.utils.translation import gettext_lazy as _
from asgiref.sync import sync_to_async

from nova.models.Tool import Tool, ToolCredential

logger = logging.getLogger(__name__)

# Cache for languages to avoid repeated API calls
_languages_cache: Optional[List[Dict[str, Union[int, str]]]] = None


async def test_judge0_access(tool: Tool) -> Dict[str, str]:
    """Test connection to Judge0 by fetching supported languages."""
    host = await get_judge0_host(tool)
    languages = await fetch_languages(host)
    count = len(languages)
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


async def api_request(method: str, url: str, headers: Optional[Dict] = None,
                      data: Optional[Dict] = None, api_key=None) -> Dict:
    """Async HTTP request helper for Judge0 API."""
    hdrs = dict(headers or {})
    if api_key:
        hdrs['X-Auth-Token'] = api_key
    async with aiohttp.ClientSession() as session:
        kwargs = {'headers': hdrs, 'json': data} if data else {'headers': hdrs}
        async with session.request(method, url, **kwargs) as response:
            if response.status != 200 and response.status != 201:
                error_text = await response.text()
                raise ValueError(f"Judge0 API error: {response.status} - {error_text}")
            return await response.json()


async def fetch_languages(host: str) -> List[Dict[str, Union[int, str]]]:
    """Fetch and cache supported languages from Judge0."""
    global _languages_cache
    if _languages_cache is None:
        try:
            response = await api_request('GET', f"{host}/languages")
            _languages_cache = response
        except Exception as e:
            logger.error(f"Error fetching languages: {str(e)}")
            _languages_cache = []
    return _languages_cache


async def list_supported_languages(host: str) -> str:
    """List supported languages with IDs and names."""
    languages = await fetch_languages(host)
    if not languages:
        return _("No languages available.")
    formatted = [f"{lang['id']}: {lang['name']}" for lang in languages]
    return ", ".join(formatted)


async def get_language_id(host: str, language: Union[str, int]) -> int:
    """Map language name or ID to Judge0 language_id. Default to Python if not specified."""
    if isinstance(language, int):
        return language

    if not language:
        language = "python"  # Default to Python

    languages = await fetch_languages(host)
    if not languages:
        raise ValueError(_("No languages available. Please check server configuration."))

    # Simple fuzzy match: lowercase, contains
    language_lower = language.lower()
    matches = [lang['id'] for lang in languages if language_lower in lang['name'].lower()]

    if not matches:
        raise ValueError(_("Language '{lang}' not found.\
            Use list_supported_languages to see available options.").format(lang=language))

    # Prefer the highest ID (usually newest version) if multiple matches
    return max(matches)


async def get_execution_status(host: str, token: str) -> str:
    """Get status of a submission."""
    response = await api_request('GET', f"{host}/submissions/{token}?base64_encoded=true")
    status = response.get('status', {}).get('description', 'Unknown')
    stdout = response.get('stdout', '')
    decoded_stdout = base64.b64decode(stdout).decode('utf-8') if stdout else ''
    stderr = response.get('stderr', '')
    decoded_stderr = base64.b64decode(stderr).decode('utf-8') if stderr else ''
    return f"Status: {status}\nStdout: {decoded_stdout}\nStderr: {decoded_stderr}"


async def compile_code(host: str, code: str, language: Union[str, int]) -> str:
    """Compile code without execution (for compiled languages)."""
    language_id = await get_language_id(host, language)
    encoded_code = base64.b64encode(code.encode('utf-8')).decode('utf-8')
    data = {
        "source_code": encoded_code,
        "language_id": language_id,
        "compiler_options": "",
        "command_line_arguments": ""
    }
    response = await api_request('POST', f"{host}/submissions?base64_encoded=true&wait=false", data=data)
    token = response.get('token')

    # Poll for completion
    for _i in range(10):  # Max 10 attempts
        status = await get_execution_status(host, token)
        if "Status: Accepted" in status or "Status: Compilation Error" in status:
            return status
        await asyncio.sleep(1)

    return _("Compilation timeout")


async def execute_code(host: str, code: str, language: Union[str, int] = "python",
                       input_data: Optional[str] = None, timeout: int = 5) -> str:
    """Execute code and return output."""
    language_id = await get_language_id(host, language)
    encoded_code = base64.b64encode(code.encode('utf-8')).decode('utf-8')
    encoded_input_data = base64.b64encode(input_data.encode('utf-8')).decode('utf-8') if input_data else ""
    data = {
        "source_code": encoded_code,
        "language_id": language_id,
        "stdin": encoded_input_data,
        "cpu_time_limit": timeout,
        "memory_limit": 128000  # 128MB default
    }
    response = await api_request('POST', f"{host}/submissions?base64_encoded=true&wait=true", data=data)
    stdout = response.get('stdout', '')
    decoded_stdout = base64.b64decode(stdout).decode('utf-8') if stdout else ''
    stderr = response.get('stderr', '')
    decoded_stderr = base64.b64decode(stderr).decode('utf-8') if stderr else ''
    status = response.get('status', {}).get('description', 'Unknown')
    return f"Status: {status}\nStdout: {decoded_stdout}\nStderr: {decoded_stderr}"


async def run_code_with_input(host: str, code: str, language: Union[str, int] = "python",
                              inputs: List[str] = None) -> str:
    """Run code with multiple inputs."""
    if inputs is None:
        inputs = []
    language_id = await get_language_id(host, language)
    encoded_code = base64.b64encode(code.encode('utf-8')).decode('utf-8')
    results = []
    for inp in inputs:
        encoded_input_data = base64.b64encode(inp.encode('utf-8')).decode('utf-8') if inp else ""
        data = {
            "source_code": encoded_code,
            "language_id": language_id,
            "stdin": encoded_input_data,
            "cpu_time_limit": 5,  # Use default timeout
            "memory_limit": 128000
        }
        response = await api_request('POST', f"{host}/submissions?base64_encoded=true&wait=true", data=data)
        stdout = response.get('stdout', '')
        decoded_stdout = base64.b64decode(stdout).decode('utf-8') if stdout else ''
        stderr = response.get('stderr', '')
        decoded_stderr = base64.b64decode(stderr).decode('utf-8') if stderr else ''
        status = response.get('status', {}).get('description', 'Unknown')
        results.append(f"Input: {inp}\nStatus: {status}\nStdout: {decoded_stdout}\nStderr: {decoded_stderr}")
    return "\n\n".join(results)
