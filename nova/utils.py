import asyncio
import bleach
import logging
import re
from markdown import markdown
from asgiref.sync import async_to_sync
from urllib.parse import urlparse, urlunparse
from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from langchain_core.messages import BaseMessage


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


def validate_relaxed_url(value):
    """
    Simple validator for relaxed URLs:
    This allows single-label hosts like 'langfuse:3000'.
    Checks for scheme (http/https), host, optional port/path.
    """
    if not value:
        return  # Allow empty if blank=True

    # Relaxed regex: scheme://host[:port][/path]
    regex = re.compile(
        r'^(https?://)'  # Scheme (http or https)
        r'([a-z0-9-]+(?:\.[a-z0-9-]+)*|localhost)'  # Host
        r'(?::\d{1,5})?'  # Optional port
        r'(?:/[^\s]*)?$'  # Optional path
    )
    if not regex.match(value):
        raise ValidationError(_("Enter a valid URL."))


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


def markdown_to_html(markdown_text: str) -> str:
    raw_html = markdown(markdown_text,
                        extensions=MARKDOWN_EXTENSIONS,
                        extension_configs=MARKDOWN_EXTENSION_CONFIGS)
    clean_html = bleach.clean(raw_html,
                              tags=ALLOWED_TAGS,
                              attributes=ALLOWED_ATTRS,
                              strip=True)
    return mark_safe(clean_html)
