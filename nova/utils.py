import bleach
import logging
import re

from markdown import markdown
from urllib.parse import urlparse, urlunparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _
from langchain_core.messages import BaseMessage


# Chat-oriented markdown profile: robust list/code/table rendering without TOC noise.
MARKDOWN_EXTENSIONS = [
    "extra",
    "sane_lists",
    "md_in_html",
    "nl2br",
]

MARKDOWN_EXTENSION_CONFIGS = {}
MARKDOWN_TAB_LENGTH = 2

ALLOWED_TAGS = [
    "p", "strong", "em", "ul", "ol", "li", "code", "pre", "blockquote",
    "br", "hr", "a",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "del", "kbd", "sup", "sub",
    # Table support
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
    "code": ["class"],
    "pre": ["class"],
}

logger = logging.getLogger(__name__)

_THINK_BLOCK_PATTERNS = (
    re.compile(r"\[THINK\].*?\[/THINK\]", flags=re.IGNORECASE | re.DOTALL),
    re.compile(r"<think>.*?</think>", flags=re.IGNORECASE | re.DOTALL),
)

_LIST_ITEM_RE = re.compile(r"^(?P<indent>\s*)(?:[-*+]\s+|\d+[.)]\s+).+")
_TABLE_ROW_RE = re.compile(r"^\|.*\|\s*$")
_TABLE_SEPARATOR_RE = re.compile(r"^\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)*\s*\|?\s*$")


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
    Simple validator for relaxed URLs.

    This allows single-label hosts like ``nova:3000`` while still requiring
    an explicit http/https scheme.
    """
    if not value:
        return

    regex = re.compile(
        r'^(https?://)'
        r'([a-z0-9-]+(?:\.[a-z0-9-]+)*|localhost)'
        r'(?::\d{1,5})?'
        r'(?:/[^\s]*)?$'
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


def strip_thinking_blocks(text: str | None) -> str:
    """Remove internal reasoning blocks from model output."""
    cleaned = "" if text is None else str(text)
    if not cleaned:
        return ""

    for pattern in _THINK_BLOCK_PATTERNS:
        cleaned = pattern.sub("", cleaned)

    # Best-effort cleanup when tags are unbalanced.
    cleaned = re.sub(r"\[THINK\].*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"<think>.*$", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r"\[/?THINK\]", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"</?think>", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _is_markdown_table_line(stripped_line: str) -> bool:
    if not stripped_line:
        return False
    return bool(_TABLE_ROW_RE.match(stripped_line) or _TABLE_SEPARATOR_RE.match(stripped_line))


def _normalize_list_nested_tables(markdown_text: str) -> str:
    """Make markdown tables inside list items render reliably.

    Python-Markdown's table handling inside list items is strict: table blocks need
    an empty line before the table and a deeper indentation than the list marker.
    LLM outputs often omit one or both, so we normalize the common pattern.
    """
    lines = markdown_text.splitlines()
    if not lines:
        return markdown_text

    normalized: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        normalized.append(line)

        list_match = _LIST_ITEM_RE.match(line)
        if not list_match:
            i += 1
            continue

        list_indent = len(list_match.group("indent"))
        table_indent = " " * (list_indent + MARKDOWN_TAB_LENGTH)

        j = i + 1
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            i += 1
            continue

        candidate = lines[j]
        candidate_stripped = candidate.lstrip()
        candidate_indent = len(candidate) - len(candidate_stripped)
        if candidate_indent <= list_indent or not _is_markdown_table_line(candidate_stripped):
            i += 1
            continue

        if normalized and normalized[-1].strip():
            normalized.append("")

        while j < len(lines):
            current = lines[j]
            current_stripped = current.lstrip()
            current_indent = len(current) - len(current_stripped)

            if not current_stripped:
                normalized.append("")
                j += 1
                continue
            if current_indent <= list_indent or not _is_markdown_table_line(current_stripped):
                break

            normalized.append(f"{table_indent}{current_stripped}")
            j += 1

        i = j

    return "\n".join(normalized)


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


def markdown_to_html(markdown_text: str) -> str:
    normalized_markdown = _normalize_list_nested_tables(markdown_text)
    raw_html = markdown(
        normalized_markdown,
        extensions=MARKDOWN_EXTENSIONS,
        extension_configs=MARKDOWN_EXTENSION_CONFIGS,
        tab_length=MARKDOWN_TAB_LENGTH,
    )
    clean_html = bleach.clean(raw_html,
                              tags=ALLOWED_TAGS,
                              attributes=ALLOWED_ATTRS,
                              strip=True)
    return mark_safe(clean_html)


def compute_external_base() -> str | None:
    """
    Compute a robust external base URL for this deployment.

    Preference order:
    1. First CSRF_TRUSTED_ORIGIN (if set), e.g. "https://example.com"
    2. Single ALLOWED_HOSTS entry (non-wildcard), with scheme:
       - if value already contains scheme, keep it
       - else:
         - https when SECURE_SSL_REDIRECT or not DEBUG
         - http otherwise

    Returns:
        Absolute base URL without trailing slash, or None when no suitable
        configuration is found.
    """
    base = None

    # 1) CSRF_TRUSTED_ORIGINS
    origins = getattr(settings, "CSRF_TRUSTED_ORIGINS", None) or []
    if origins:
        base = origins[0].rstrip("/")

    # 2) ALLOWED_HOSTS heuristic (single non-wildcard host)
    if not base:
        hosts = [h for h in getattr(settings, "ALLOWED_HOSTS", []) if h and "*" not in h]
        if len(hosts) == 1:
            host = hosts[0]
            if host.startswith("http://") or host.startswith("https://"):
                base = host.rstrip("/")
            else:
                use_https = getattr(settings, "SECURE_SSL_REDIRECT", False) or not getattr(settings, "DEBUG", False)
                scheme = "https" if use_https else "http"
                base = f"{scheme}://{host}".rstrip("/")

    return base


def compute_webapp_public_url(slug: str) -> str:
    """
    Compute a robust public URL for a WebApp using compute_external_base().

    Preference order:
    1. External base (CSRF_TRUSTED_ORIGINS / ALLOWED_HOSTS heuristic)
    2. Fallback to relative path: /apps/<slug>/

    Always returns a URL ending with "/apps/<slug>/".
    """
    base = compute_external_base()

    # Fallback: relative URL only
    if not base:
        return f"/apps/{slug}/"

    return f"{base}/apps/{slug}/"
