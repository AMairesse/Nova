import re
from urllib.parse import urlparse, urlunparse
from typing import Tuple

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