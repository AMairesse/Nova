import re
from typing import List
from urllib.parse import urlparse

import httpx
from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
from langchain_core.tools import StructuredTool
from playwright.async_api import async_playwright

from nova.external_files import (
    build_artifact_tool_payload,
    get_external_file_import_max_size_bytes,
    stage_external_files_as_artifacts,
)
from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool


METADATA = {
    'name': 'Browser',
    'description': 'Interact with web pages using PlayWright (navigate, extract text, etc.)',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}


_FILENAME_RE = re.compile(r'filename\*?=(?:UTF-8\'\')?"?([^\";]+)"?')


def get_prompt_instructions() -> List[str]:
    return [
        "Use web_download_file when the user needs the actual file, not just a page summary.",
        "Downloaded files become temporary conversation artifacts that can be reused or emailed.",
    ]


def _infer_download_filename(url: str, headers, explicit_filename: str = "") -> str:
    provided = str(explicit_filename or "").strip()
    if provided:
        return provided

    content_disposition = str(headers.get("content-disposition") or "").strip()
    if content_disposition:
        match = _FILENAME_RE.search(content_disposition)
        if match:
            candidate = str(match.group(1) or "").strip().strip('"')
            if candidate:
                return candidate

    path = urlparse(str(url or "")).path
    candidate = path.rsplit("/", 1)[-1] if path else ""
    return candidate or "downloaded-file"


async def web_download_file(agent: LLMAgent, url: str, filename: str = ""):
    if getattr(agent, "thread", None) is None:
        return "Web download requires an active conversation thread.", None

    max_size = get_external_file_import_max_size_bytes()
    timeout = httpx.Timeout(60.0, connect=10.0)
    bytes_read = 0
    chunks: list[bytes] = []

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            inferred_name = _infer_download_filename(url, response.headers, filename)
            mime_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            async for chunk in response.aiter_bytes():
                if not chunk:
                    continue
                bytes_read += len(chunk)
                if bytes_read > max_size:
                    return (
                        f"Downloaded file exceeds the {max_size} byte limit.",
                        None,
                    )
                chunks.append(chunk)

    artifacts, errors = await stage_external_files_as_artifacts(
        agent,
        [
            {
                "filename": inferred_name,
                "content": b"".join(chunks),
                "mime_type": mime_type,
                "origin_locator": {"url": url},
            }
        ],
        origin_type="web",
        imported_by_tool="web_download_file",
        source="web",
    )
    if errors and not artifacts:
        return f"Failed to download file: {'; '.join(errors)}", None

    artifact = artifacts[0] if artifacts else None
    message = (
        f"Downloaded file {getattr(artifact, 'filename', inferred_name)} from {url}."
        if artifact is not None
        else f"Downloaded file from {url}."
    )
    if errors:
        message += f" Warnings: {'; '.join(errors)}"
    return message, build_artifact_tool_payload(artifacts, tool_output=True)


async def init(agent: LLMAgent) -> None:
    """
    Init the browser and store it in agent._resources.
    """
    if 'browser' in agent._resources:
        return

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)
    agent._resources['playwright_async'] = playwright
    agent._resources['browser'] = browser


async def close(agent: LLMAgent) -> None:
    """
    Close the browser and clean agent._resources.
    """
    browser = agent._resources.get('browser')
    playwright = agent._resources.get('playwright_async')
    if browser:
        try:
            if browser.is_connected():
                await browser.close()
        finally:
            del agent._resources['browser']
    if playwright:
        try:
            await playwright.stop()
        finally:
            del agent._resources['playwright_async']


async def get_functions(tool: Tool, agent: LLMAgent) -> List:
    browser = agent._resources.get('browser')
    if not browser:
        raise ValueError("Browser not initialized. Ensure init() was called.")

    toolkit = PlayWrightBrowserToolkit.from_browser(async_browser=browser)
    async def _download_wrapper(url: str, filename: str = ""):
        return await web_download_file(agent, url, filename)

    tools = list(toolkit.get_tools())
    tools.append(
        StructuredTool.from_function(
            coroutine=_download_wrapper,
            name="web_download_file",
            description="Download an HTTP(S) file into the current conversation as a reusable artifact.",
            args_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "direct HTTP(S) file URL",
                    },
                    "filename": {
                        "type": "string",
                        "description": "optional filename override",
                    },
                },
                "required": ["url"],
            },
            return_direct=True,
            response_format="content_and_artifact",
        )
    )
    return tools
