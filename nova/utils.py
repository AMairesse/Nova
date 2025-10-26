import asyncio
import logging
import bleach
from markdown import markdown
from asgiref.sync import async_to_sync
from urllib.parse import urlparse, urlunparse
from django.conf import settings
from django.utils.safestring import mark_safe
from nova.models.models import LLMProvider, ProviderType, Tool, ToolCredential
from langchain_core.messages import BaseMessage

OLLAMA_SERVER_URL = settings.OLLAMA_SERVER_URL
OLLAMA_MODEL_NAME = settings.OLLAMA_MODEL_NAME
OLLAMA_CONTEXT_LENGTH = settings.OLLAMA_CONTEXT_LENGTH
SEARNGX_SERVER_URL = settings.SEARNGX_SERVER_URL
SEARNGX_NUM_RESULTS = settings.SEARNGX_NUM_RESULTS
JUDGE0_SERVER_URL = settings.JUDGE0_SERVER_URL

# Markdown configuration for better list handling
MARKDOWN_EXTENSIONS = [
    "extra",           # Basic extensions (tables, fenced code, etc.)
    "toc",             # Table of contents (includes better list processing)
    "sane_lists",      # Improved list handling
    "md_in_html",      # Allow markdown inside HTML
]

MARKDOWN_EXTENSION_CONFIGS = {
    'toc': {
        'marker': ''  # Disable TOC markers to avoid conflicts
    }
}

ALLOWED_TAGS = [
    "p", "strong", "em", "ul", "ol", "li", "code", "pre", "blockquote",
    "br", "hr", "a",
    # Table support
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
}

logger = logging.getLogger(__name__)


def normalize_url(urlish) -> str:
    """
    Return a canonical representation of *urlish*.

    • Accepts any object with a usable str() representation.
    • Removes the default port (80/443) when it matches the scheme.
    • Adds a trailing slash *only* when the path is empty.
    • Preserves path, query, params and fragment exactly as provided.
    """
    # 1. Parse
    parsed = urlparse(str(urlish))
    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""
    port = parsed.port
    params = parsed.params
    path = parsed.path
    query = parsed.query
    fragment = parsed.fragment

    # 2. Strip default ports
    if port and ((scheme == "https" and port == 443) or (scheme == "http" and port == 80)):
        port = None

    netloc = f"{host}:{port}" if port else host

    # 3. Add “/” *only* when path is empty
    if path == "":
        path = "/"

    # 4. Re-assemble, keeping every component
    return urlunparse((scheme, netloc, path, params, query, fragment))


def extract_final_answer(output):
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        last = next((m for m in reversed(output) if isinstance(m, BaseMessage)), None)
        return last.content if last else str(output)
    if isinstance(output, dict) and "messages" in output:
        return extract_final_answer(output["messages"])
    return str(output)


def get_theme_content(content: str, theme: str) -> str:
    """
    Extract content for a specific theme from Markdown content.
    Looks for headers starting with '# ' followed by the theme name.
    """
    lines = content.split('\n')
    theme_content = []
    in_theme = False

    for line in lines:
        if line.strip().startswith('# ') and line.strip()[2:].strip() == theme:
            in_theme = True
        elif line.strip().startswith('# ') and in_theme:
            break
        elif in_theme:
            theme_content.append(line)

    return '\n'.join(theme_content).strip()


def estimate_tokens(text: str = None, input_size: int = None) -> int:
    """Simple token estimation: approx 1 token per 4 chars."""
    if input_size:
        return input_size // 4 + 1
    elif text is not None:
        return len(text) // 4 + 1
    else:
        return 0


def schedule_in_event_loop(coro):
    async def _runner():
        asyncio.create_task(coro)

    async_to_sync(_runner)()


def check_and_create_system_provider():
    # Get the system provider if it exists
    provider = LLMProvider.objects.filter(user=None,
                                          name='System - Ollama',
                                          provider_type=ProviderType.OLLAMA).first()
    if OLLAMA_SERVER_URL and OLLAMA_MODEL_NAME:
        # Create a "system provider" if it doesn't already exist
        if not provider:
            LLMProvider.objects.create(user=None,
                                       name='System - Ollama',
                                       provider_type=ProviderType.OLLAMA,
                                       model=OLLAMA_MODEL_NAME,
                                       base_url=OLLAMA_SERVER_URL,
                                       max_context_tokens=OLLAMA_CONTEXT_LENGTH)
        else:
            # Update it if needed
            if provider.model != OLLAMA_MODEL_NAME or \
               provider.base_url != OLLAMA_SERVER_URL or \
               provider.max_context_tokens != OLLAMA_CONTEXT_LENGTH:
                provider.model = OLLAMA_MODEL_NAME
                provider.base_url = OLLAMA_SERVER_URL
                provider.max_context_tokens = OLLAMA_CONTEXT_LENGTH
                provider.save()
    else:
        if LLMProvider.objects.filter(user=None,
                                      provider_type=ProviderType.OLLAMA).exists():
            # If the system provider is not used then delete it
            if not provider.agents.exists():
                provider.delete()
            else:
                logger.warning(
                    """WARNING: OLLAMA_SERVER_URL or OLLAMA_MODEL_NAME not set, but a system
                       provider exists and is being used by at least one agent.""")


def check_and_create_searxng_tool():
    # Get the searxng's system tool if it exists
    tool = Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='searxng').first()

    if SEARNGX_SERVER_URL and SEARNGX_NUM_RESULTS:
        # Create a "system tool" if it doesn't already exist
        if not tool:
            tool = Tool.objects.create(user=None,
                                       name='System - SearXNG',
                                       tool_type=Tool.ToolType.BUILTIN,
                                       tool_subtype='searxng',
                                       python_path='nova.tools.builtins.searxng')
            ToolCredential.objects.create(user=None,
                                          tool=tool,
                                          config={'searxng_url': SEARNGX_SERVER_URL,
                                                  'num_results': SEARNGX_NUM_RESULTS})
        else:
            cred = ToolCredential.objects.filter(tool=tool).first()
            if not cred:
                ToolCredential.objects.create(user=None,
                                              tool=tool,
                                              config={'searxng_url': SEARNGX_SERVER_URL,
                                                      'num_results': SEARNGX_NUM_RESULTS})
            else:
                # Update it if needed
                if cred.config.get('searxng_url') != SEARNGX_SERVER_URL or \
                   cred.config.get('num_results') != SEARNGX_NUM_RESULTS:
                    cred.config['searxng_url'] = SEARNGX_SERVER_URL
                    cred.config['num_results'] = SEARNGX_NUM_RESULTS
                    cred.save()
    else:
        if Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='searxng').exists():
            # If the system tool is not used then delete it
            if not tool.agents.exists():
                tool.delete()
            else:
                logger.warning(
                    """WARNING: SEARXNG_SERVER_URL not set, but a system
                       tool exists and is being used by at least one agent.""")


def check_and_create_judge0_tool():
    # Get the judge0's system tool if it exists
    tool = Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='code_execution').first()

    if JUDGE0_SERVER_URL:
        # Create a "system tool" if it doesn't already exist
        if not tool:
            tool = Tool.objects.create(user=None,
                                       name='System - Code Execution',
                                       tool_type=Tool.ToolType.BUILTIN,
                                       tool_subtype='code_execution',
                                       python_path='nova.tools.builtins.code_execution')
            ToolCredential.objects.create(user=None,
                                          tool=tool,
                                          config={'judge0_url': JUDGE0_SERVER_URL})
        else:
            cred = ToolCredential.objects.filter(tool=tool).first()
            if not cred:
                ToolCredential.objects.create(user=None,
                                              tool=tool,
                                              config={'judge0_url': JUDGE0_SERVER_URL})
            else:
                # Update it if needed
                if cred.config.get('judge0_url') != JUDGE0_SERVER_URL:
                    cred.config['judge0_url'] = JUDGE0_SERVER_URL
                    cred.save()
    else:
        if Tool.objects.filter(user=None,
                               tool_type=Tool.ToolType.BUILTIN,
                               tool_subtype='code_execution').exists():
            # If the system tool is not used then delete it
            if not tool.agents.exists():
                tool.delete()
            else:
                logger.warning(
                    """WARNING: JUDGE0_SERVER_URL not set, but a system
                       tool exists and is being used by at least one agent.""")


def markdown_to_html(markdown_text: str) -> str:
    raw_html = markdown(markdown_text,
                        extensions=MARKDOWN_EXTENSIONS,
                        extension_configs=MARKDOWN_EXTENSION_CONFIGS)
    clean_html = bleach.clean(raw_html,
                              tags=ALLOWED_TAGS,
                              attributes=ALLOWED_ATTRS,
                              strip=True)
    return mark_safe(clean_html)
