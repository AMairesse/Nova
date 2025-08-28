import asyncio
import logging
from asgiref.sync import async_to_sync
from urllib.parse import urlparse, urlunparse
from django.conf import settings
from nova.models.models import LLMProvider, ProviderType, Tool, ToolCredential
from langchain_core.messages import BaseMessage

OLLAMA_SERVER_URL = settings.OLLAMA_SERVER_URL
OLLAMA_MODEL_NAME = settings.OLLAMA_MODEL_NAME
OLLAMA_CONTEXT_LENGTH = settings.OLLAMA_CONTEXT_LENGTH
SEARNGX_SERVER_URL = settings.SEARNGX_SERVER_URL
SEARNGX_NUM_RESULTS = settings.SEARNGX_NUM_RESULTS

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


def estimate_tokens(text: str = None, input_size: int = None) -> int:
    """Simple token estimation: approx 1 token per 4 chars."""
    if input_size:
        return input_size // 4 + 1
    elif text is not None:
        return len(text) // 4 + 1
    else:
        return 0


def schedule_in_event_loop(coro):
    """
    Planifie la coroutine `coro` dans la boucle ASGI principale
    sans bloquer la vue synchrone.
    """
    async def _runner():
        # on est DÉJÀ dans la boucle ASGI → create_task fonctionne
        asyncio.create_task(coro)

    # async_to_sync exécute _runner dans la boucle principale
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
