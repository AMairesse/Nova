import re
from urllib.parse import urlparse, urlunparse
from typing import Tuple, List
from langchain_core.messages import BaseMessage

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
    scheme  = parsed.scheme.lower()
    host    = parsed.hostname or ""
    port    = parsed.port
    params  = parsed.params
    path    = parsed.path
    query   = parsed.query
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
    from langchain_core.messages import BaseMessage
    if isinstance(output, str):
        return output
    if isinstance(output, list):
        last = next((m for m in reversed(output) if isinstance(m, BaseMessage)), None)
        return last.content if last else str(output)
    if isinstance(output, dict) and "messages" in output:
        return extract_final_answer(output["messages"])
    return str(output)

def estimate_tokens(text: str=None, input_size: int=None) -> int:
    """Simple token estimation: approx 1 token per 4 chars."""
    if input_size:
        return input_size // 4 + 1
    elif text is not None:
        return len(text) // 4 + 1
    else:
        return 0

def estimate_total_context(agent: 'LLMAgent') -> int:
    """Estimate tokens for system_prompt + tools desc + history."""
    total = 0
    # System prompt
    total += estimate_tokens(agent.build_system_prompt())
    # Tools desc (approx: sum of descriptions from agent.tools - maintenant disponible)
    tools_desc = " ".join([t.description for t in getattr(agent, 'tools', [])])  # Check safe
    total += estimate_tokens(tools_desc)
    # History (sum message contents from state via checkpointer)
    state = agent.agent.get_state(agent.config)  # Accès à l'état courant
    messages = state.values.get('messages', [])  # Liste des BaseMessage
    for msg in messages:
        if isinstance(msg, BaseMessage):
            total += estimate_tokens(msg.content)
    return total