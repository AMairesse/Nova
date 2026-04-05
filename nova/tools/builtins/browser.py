from typing import List

from langchain_community.agent_toolkits import PlayWrightBrowserToolkit
from langchain_core.tools import StructuredTool
from playwright.async_api import async_playwright

from nova.external_files import (
    build_artifact_tool_payload,
    stage_external_files_as_artifacts,
)
from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool
from nova.web.download_service import download_http_file, infer_download_filename


METADATA = {
    'name': 'Browser',
    'description': 'Interact with web pages using PlayWright (navigate, extract text, etc.)',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}


def get_prompt_instructions() -> List[str]:
    return [
        "Use web_download_file when the user needs the actual file, not just a page summary.",
        "Downloaded files become temporary conversation artifacts that can be reused or emailed.",
    ]


def _infer_download_filename(url: str, headers, explicit_filename: str = "") -> str:
    return infer_download_filename(url, headers, explicit_filename)


async def web_download_file(agent: LLMAgent, url: str, filename: str = ""):
    if getattr(agent, "thread", None) is None:
        return "Web download requires an active conversation thread.", None

    try:
        downloaded = await download_http_file(url, filename=filename)
    except ValueError as exc:
        return str(exc), None

    artifacts, errors = await stage_external_files_as_artifacts(
        agent,
        [
            {
                "filename": downloaded["filename"],
                "content": downloaded["content"],
                "mime_type": downloaded["mime_type"],
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
        f"Downloaded file {getattr(artifact, 'filename', downloaded['filename'])} from {url}."
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
