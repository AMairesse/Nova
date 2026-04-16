from __future__ import annotations

import mimetypes
import posixpath
import re
import xml.etree.ElementTree as etree
from dataclasses import dataclass
from typing import Iterable

import bleach
from markdown import Markdown
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

from django.urls import reverse
from django.utils.safestring import mark_safe

from nova.models.UserFile import UserFile
from nova.utils import (
    ALLOWED_ATTRS,
    ALLOWED_TAGS,
    MARKDOWN_EXTENSION_CONFIGS,
    MARKDOWN_EXTENSIONS,
    MARKDOWN_TAB_LENGTH,
    _normalize_list_nested_tables,
)


_MARKDOWN_IMAGE_TARGET_RE = re.compile(r"!\[[^\]]*]\((?P<target>/[^)\n]+)\)")
_MARKDOWN_LINK_TARGET_RE = re.compile(r"(?<!!)\[[^\]]+]\((?P<target>/[^)\n]+)\)")


@dataclass(frozen=True, slots=True)
class ResolvedMarkdownTarget:
    path: str
    user_file_id: int
    mime_type: str
    content_url: str

    @property
    def is_image(self) -> bool:
        normalized_mime = str(self.mime_type or "").strip().lower()
        if normalized_mime.startswith("image/"):
            return True
        if normalized_mime and normalized_mime != "application/octet-stream":
            return False
        guessed_mime, _ = mimetypes.guess_type(self.path)
        return str(guessed_mime or "").strip().lower().startswith("image/")


def _normalize_vfs_target(target: str | None) -> str | None:
    raw = str(target or "").strip()
    if not raw or not raw.startswith("/") or raw.startswith("//"):
        return None
    normalized = posixpath.normpath(raw)
    if not normalized.startswith("/"):
        return None
    return normalized


def _iter_markdown_vfs_targets(markdown_text: str | None) -> Iterable[str]:
    text = str(markdown_text or "")
    for pattern in (_MARKDOWN_IMAGE_TARGET_RE, _MARKDOWN_LINK_TARGET_RE):
        for match in pattern.finditer(text):
            normalized = _normalize_vfs_target(match.group("target"))
            if normalized:
                yield normalized


def collect_markdown_vfs_targets(markdown_text: str | None) -> set[str]:
    return set(_iter_markdown_vfs_targets(markdown_text))


def extract_markdown_vfs_image_paths(markdown_text: str | None) -> list[str]:
    text = str(markdown_text or "")
    paths: list[str] = []
    seen: set[str] = set()
    for match in _MARKDOWN_IMAGE_TARGET_RE.finditer(text):
        normalized = _normalize_vfs_target(match.group("target"))
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        paths.append(normalized)
    return paths


def resolve_markdown_vfs_targets(
    markdown_texts: Iterable[str | None],
    *,
    user,
    thread,
) -> dict[str, ResolvedMarkdownTarget]:
    paths: set[str] = set()
    for markdown_text in markdown_texts:
        paths.update(collect_markdown_vfs_targets(markdown_text))
    if not paths:
        return {}

    resolved: dict[str, ResolvedMarkdownTarget] = {}
    for user_file in UserFile.objects.filter(
        user=user,
        thread=thread,
        scope=UserFile.Scope.THREAD_SHARED,
        original_filename__in=sorted(paths),
    ):
        resolved[str(user_file.original_filename)] = ResolvedMarkdownTarget(
            path=str(user_file.original_filename),
            user_file_id=int(user_file.id),
            mime_type=str(user_file.mime_type or "").strip(),
            content_url=reverse("file_content", args=[user_file.id]),
        )
    return resolved


class _AgentMarkdownTreeprocessor(Treeprocessor):
    def __init__(self, md, *, resolved_targets: dict[str, ResolvedMarkdownTarget]):
        super().__init__(md)
        self.resolved_targets = resolved_targets

    @staticmethod
    def _build_unavailable_image_element(path: str, tail: str | None) -> etree.Element:
        element = etree.Element("em")
        element.text = f"Image unavailable: {path or 'missing image'}"
        element.tail = tail
        return element

    def _rewrite_link(self, element: etree.Element) -> None:
        href = str(element.attrib.get("href") or "").strip()
        normalized = _normalize_vfs_target(href)
        if normalized is None:
            return

        target = self.resolved_targets.get(normalized)
        if target is None:
            element.attrib.pop("href", None)
            element.attrib.pop("title", None)
            return

        element.attrib["href"] = target.content_url
        element.attrib["rel"] = "noopener noreferrer"

    def _rewrite_image(self, element: etree.Element) -> etree.Element | None:
        src = str(element.attrib.get("src") or "").strip()
        normalized = _normalize_vfs_target(src)
        if normalized is None:
            return self._build_unavailable_image_element(src, element.tail)

        target = self.resolved_targets.get(normalized)
        if target is None or not target.is_image:
            return self._build_unavailable_image_element(normalized, element.tail)

        element.attrib["src"] = target.content_url
        return None

    def _rewrite_children(self, parent: etree.Element) -> None:
        children = list(parent)
        for index, child in enumerate(children):
            self._rewrite_children(child)
            if child.tag == "a":
                self._rewrite_link(child)
                continue
            if child.tag != "img":
                continue
            replacement = self._rewrite_image(child)
            if replacement is None:
                continue
            parent.remove(child)
            parent.insert(index, replacement)

    def run(self, root: etree.Element) -> etree.Element:
        self._rewrite_children(root)
        return root


class _AgentMarkdownExtension(Extension):
    def __init__(self, *, resolved_targets: dict[str, ResolvedMarkdownTarget]):
        super().__init__()
        self.resolved_targets = resolved_targets

    def extendMarkdown(self, md: Markdown) -> None:
        md.treeprocessors.register(
            _AgentMarkdownTreeprocessor(md, resolved_targets=self.resolved_targets),
            "nova_agent_markdown_targets",
            15,
        )


def render_agent_markdown(
    markdown_text: str,
    *,
    user=None,
    thread=None,
    resolved_targets: dict[str, ResolvedMarkdownTarget] | None = None,
) -> str:
    targets = dict(resolved_targets or {})
    if not targets and user is not None and thread is not None:
        targets = resolve_markdown_vfs_targets([markdown_text], user=user, thread=thread)

    md = Markdown(
        extensions=[
            *MARKDOWN_EXTENSIONS,
            _AgentMarkdownExtension(resolved_targets=targets),
        ],
        extension_configs=MARKDOWN_EXTENSION_CONFIGS,
        tab_length=MARKDOWN_TAB_LENGTH,
    )
    raw_html = md.convert(_normalize_list_nested_tables(markdown_text))
    allowed_tags = list(ALLOWED_TAGS) + ["img"]
    allowed_attrs = dict(ALLOWED_ATTRS)
    allowed_attrs["img"] = ["src", "alt", "title"]
    clean_html = bleach.clean(
        raw_html,
        tags=allowed_tags,
        attributes=allowed_attrs,
        strip=True,
    )
    return mark_safe(clean_html)
