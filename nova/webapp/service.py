from __future__ import annotations

import mimetypes
import posixpath
from dataclasses import dataclass
from typing import Any

from asgiref.sync import async_to_sync, sync_to_async
from channels.layers import get_channel_layer
from django.core.exceptions import ValidationError
from django.utils import timezone

from nova.file_utils import download_file_content
from nova.models.UserFile import UserFile
from nova.models.WebApp import WebApp
from nova.realtime.sidebar_updates import publish_webapps_update
from nova.utils import compute_webapp_public_url


FORBIDDEN_SOURCE_ROOTS = ("/skills", "/tmp", "/memory", "/webdav")
ALLOWED_PUBLIC_EXTENSIONS = {
    ".html",
    ".css",
    ".js",
    ".mjs",
    ".json",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".txt",
    ".webmanifest",
}
TEXT_MIME_PREFIXES = ("text/",)
TEXT_MIME_TYPES = {
    "application/javascript",
    "application/json",
    "application/manifest+json",
}
ESCAPED_HTML_PREFIXES = (
    "&lt;!doctype",
    "&lt;html",
)
LITERAL_HTML_PREFIXES = (
    "<!doctype",
    "<html",
)


class WebAppServiceError(ValueError):
    pass


@dataclass(slots=True)
class LiveWebAppFile:
    webapp: WebApp
    user_file: UserFile
    relative_path: str
    mime_type: str


def normalize_source_root(raw_path: str) -> str:
    candidate = str(raw_path or "").strip()
    if not candidate:
        raise WebAppServiceError("A source directory is required.")
    if not candidate.startswith("/"):
        candidate = f"/{candidate}"
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    for reserved in FORBIDDEN_SOURCE_ROOTS:
        if normalized == reserved or normalized.startswith(f"{reserved}/"):
            raise WebAppServiceError(f"Webapps cannot be exposed from {reserved}.")
    return normalized


def normalize_entry_path(raw_path: str) -> str:
    candidate = str(raw_path or "").strip().replace("\\", "/")
    if not candidate:
        raise WebAppServiceError("A relative entry path is required.")
    normalized = posixpath.normpath(candidate)
    if normalized in {"", ".", "/", ".."} or normalized.startswith("../"):
        raise WebAppServiceError("The entry path must stay inside the source directory.")
    if normalized.startswith("/"):
        raise WebAppServiceError("The entry path must be relative to the source directory.")
    if not normalized.lower().endswith(".html"):
        raise WebAppServiceError("The entry file must be an HTML file.")
    return normalized


def _join_source_path(source_root: str, relative_path: str) -> str:
    return posixpath.normpath(posixpath.join(source_root, relative_path))


def _is_allowed_public_extension(path: str) -> bool:
    lower = str(path or "").strip().lower()
    return any(lower.endswith(ext) for ext in ALLOWED_PUBLIC_EXTENSIONS)


def _guess_public_mime(user_file: UserFile, relative_path: str) -> str:
    mime_type = str(getattr(user_file, "mime_type", "") or "").strip().lower()
    if mime_type:
        return mime_type
    guessed, _ = mimetypes.guess_type(relative_path)
    if guessed:
        return guessed
    if relative_path.endswith(".html"):
        return "text/html"
    if relative_path.endswith(".css"):
        return "text/css"
    if relative_path.endswith(".js") or relative_path.endswith(".mjs"):
        return "application/javascript"
    return "application/octet-stream"


def _normalize_public_name(name: str | None, *, fallback: str) -> str:
    cleaned = str(name or "").strip()
    if cleaned:
        return cleaned[:120]
    fallback_name = str(fallback or "").strip()
    return (fallback_name[:120] or "WebApp").strip()


def _derive_default_name(source_root: str, entry_path: str) -> str:
    base = posixpath.basename(source_root.rstrip("/"))
    if base:
        return base
    entry_base = posixpath.basename(entry_path).rsplit(".", 1)[0]
    return entry_base or "WebApp"


def _get_webapp_sync(user, slug: str, *, thread=None) -> WebApp | None:
    query = WebApp.objects.filter(user=user, slug=slug)
    if thread is not None:
        query = query.filter(thread=thread)
    return query.first()


def _path_impacts_webapp(webapp: WebApp, path: str) -> bool:
    source_root = str(webapp.source_root or "").strip()
    normalized = posixpath.normpath(str(path or "").strip() or "/")
    if not source_root:
        return False
    if normalized == source_root:
        return True
    source_prefix = f"{source_root.rstrip('/')}/"
    normalized_prefix = f"{normalized.rstrip('/')}/"
    return normalized.startswith(source_prefix) or source_root.startswith(normalized_prefix)


def _get_live_user_file_sync(webapp: WebApp, relative_path: str) -> UserFile | None:
    normalized_relative = posixpath.normpath(str(relative_path or "").strip().replace("\\", "/"))
    if normalized_relative in {"", ".", "/", ".."} or normalized_relative.startswith("../") or normalized_relative.startswith("/"):
        return None
    full_path = _join_source_path(webapp.source_root, normalized_relative)
    return UserFile.objects.filter(
        user=webapp.user,
        thread=webapp.thread,
        scope=UserFile.Scope.THREAD_SHARED,
        original_filename=full_path,
    ).first()


def _looks_like_escaped_html(content: bytes) -> bool:
    try:
        text = content.decode("utf-8", errors="ignore").lstrip().lower()
    except Exception:
        return False
    has_escaped_prefix = any(text.startswith(prefix) for prefix in ESCAPED_HTML_PREFIXES)
    has_literal_prefix = any(text.startswith(prefix) for prefix in LITERAL_HTML_PREFIXES)
    return has_escaped_prefix and not has_literal_prefix


def _entry_file_status_sync(webapp: WebApp) -> tuple[str, str]:
    entry = str(webapp.entry_path or "").strip()
    source_root = str(webapp.source_root or "").strip()
    if not source_root or not entry:
        return "broken", "Missing source_root or entry_path."

    live_file = _get_live_user_file_sync(webapp, entry)
    if live_file is None:
        return "broken", "Entry file is missing."
    if not _is_allowed_public_extension(entry):
        return "broken", "Entry file extension is not allowed."
    if entry.lower().endswith(".html"):
        content = async_to_sync(download_file_content)(live_file)
        if _looks_like_escaped_html(content):
            return "broken", "Entry HTML appears escaped. Write raw HTML into the file."
    return "ready", ""


def _build_webapp_payload_sync(webapp: WebApp) -> dict[str, Any]:
    entry = str(webapp.entry_path or "").strip()
    source_root = str(webapp.source_root or "").strip()
    status, status_detail = _entry_file_status_sync(webapp)

    return {
        "slug": webapp.slug,
        "name": str(webapp.name or "").strip() or webapp.slug,
        "source_root": source_root,
        "entry_path": entry,
        "public_url": compute_webapp_public_url(webapp.slug),
        "status": status,
        "status_detail": status_detail,
        "updated_at": webapp.updated_at.isoformat() if webapp.updated_at else None,
    }


async def publish_webapp_update(
    *,
    thread_id: int | None,
    slug: str,
    task_id: int | str | None = None,
    channel_layer=None,
    public_url: str | None = None,
    reason: str = "webapp_update",
) -> None:
    active_channel_layer = channel_layer or get_channel_layer()
    if not active_channel_layer:
        return

    if task_id:
        if public_url:
            await active_channel_layer.group_send(
                f"task_{task_id}",
                {"type": "task_update", "message": {"type": "webapp_public_url", "slug": slug, "public_url": public_url}},
            )
        await active_channel_layer.group_send(
            f"task_{task_id}",
            {"type": "task_update", "message": {"type": "webapp_update", "slug": slug}},
        )

    if thread_id:
        await publish_webapps_update(
            thread_id,
            reason,
            slug=slug,
            channel_layer=active_channel_layer,
        )


async def list_thread_webapps(*, user, thread) -> list[dict[str, Any]]:
    def _load():
        return [
            _build_webapp_payload_sync(webapp)
            for webapp in WebApp.objects.filter(user=user, thread=thread).order_by("-updated_at", "slug")
        ]

    return await sync_to_async(_load, thread_sensitive=True)()


async def describe_webapp(*, user, thread, slug: str) -> dict[str, Any]:
    def _load():
        webapp = _get_webapp_sync(user, slug, thread=thread)
        if webapp is None:
            raise WebAppServiceError("The webapp slug provided does not exist in this conversation.")
        return _build_webapp_payload_sync(webapp)

    return await sync_to_async(_load, thread_sensitive=True)()


async def expose_webapp(
    *,
    user,
    thread,
    vfs,
    source_root: str,
    slug: str | None = None,
    name: str | None = None,
    entry_path: str | None = None,
    task_id: int | str | None = None,
    channel_layer=None,
) -> dict[str, Any]:
    normalized_root = normalize_source_root(source_root)
    if not await vfs.path_exists(normalized_root) or not await vfs.is_dir(normalized_root):
        raise WebAppServiceError(f"Source directory not found: {normalized_root}")

    async def _ensure_exposable_entry(relative_entry: str) -> None:
        entry_full_path = _join_source_path(normalized_root, relative_entry)
        entry_item = await vfs.get_real_file(entry_full_path)
        if entry_item is None or entry_item.user_file is None:
            raise WebAppServiceError(f"Entry file not found: {entry_full_path}")
        if str(entry_item.user_file.scope or "") != UserFile.Scope.THREAD_SHARED:
            raise WebAppServiceError("Webapps can only be exposed from persistent thread files.")
        if relative_entry.lower().endswith(".html"):
            content, _mime_type = await vfs.read_bytes(entry_full_path)
            if _looks_like_escaped_html(content):
                raise WebAppServiceError(
                    "Entry HTML appears escaped. Write raw HTML into the file before exposing the webapp."
                )

    if entry_path:
        resolved_entry = normalize_entry_path(entry_path)
        await _ensure_exposable_entry(resolved_entry)
    else:
        default_entry = _join_source_path(normalized_root, "index.html")
        default_item = await vfs.get_real_file(default_entry)
        if default_item is not None and default_item.user_file is not None:
            if str(default_item.user_file.scope or "") != UserFile.Scope.THREAD_SHARED:
                raise WebAppServiceError("Webapps can only be exposed from persistent thread files.")
            resolved_entry = "index.html"
        else:
            direct_html = [
                entry for entry in await vfs.list_dir(normalized_root)
                if entry.get("type") == "file" and str(entry.get("name") or "").lower().endswith(".html")
            ]
            if len(direct_html) != 1:
                raise WebAppServiceError(
                    "Could not determine the entry HTML file automatically. Use --entry <relative-path>."
                )
            candidate_name = str(direct_html[0]["name"])
            candidate_item = await vfs.get_real_file(_join_source_path(normalized_root, candidate_name))
            if candidate_item is None or candidate_item.user_file is None:
                raise WebAppServiceError("The detected entry file is not a persistent thread file.")
            if str(candidate_item.user_file.scope or "") != UserFile.Scope.THREAD_SHARED:
                raise WebAppServiceError("Webapps can only be exposed from persistent thread files.")
            resolved_entry = candidate_name
        await _ensure_exposable_entry(resolved_entry)

    def _save() -> tuple[WebApp, bool]:
        existing = _get_webapp_sync(user, slug, thread=thread) if slug else None
        conflict = (
            WebApp.objects.filter(user=user, thread=thread, source_root=normalized_root)
            .exclude(id=getattr(existing, "id", None))
            .first()
        )
        if conflict is not None:
            raise ValidationError("Another webapp in this conversation already uses this source directory.")

        display_name = _normalize_public_name(
            name,
            fallback=_derive_default_name(normalized_root, resolved_entry),
        )
        if existing is None:
            webapp = WebApp(
                user=user,
                thread=thread,
                name=display_name,
                source_root=normalized_root,
                entry_path=resolved_entry,
            )
            created = True
        else:
            webapp = existing
            webapp.name = display_name
            webapp.source_root = normalized_root
            webapp.entry_path = resolved_entry
            created = False
        webapp.full_clean()
        webapp.save()
        return webapp, created

    try:
        webapp, created = await sync_to_async(_save, thread_sensitive=True)()
    except ValidationError as exc:
        raise WebAppServiceError("; ".join(exc.messages)) from exc

    payload = await sync_to_async(_build_webapp_payload_sync, thread_sensitive=True)(webapp)
    payload["created"] = created
    await publish_webapp_update(
        thread_id=getattr(thread, "id", None),
        slug=webapp.slug,
        task_id=task_id,
        channel_layer=channel_layer,
        public_url=payload["public_url"] if created else None,
        reason="webapp_create" if created else "webapp_update",
    )
    return payload


async def delete_webapp(
    *,
    user,
    thread,
    slug: str,
    task_id: int | str | None = None,
    channel_layer=None,
) -> dict[str, str]:
    def _delete() -> bool:
        webapp = _get_webapp_sync(user, slug, thread=thread)
        if webapp is None:
            return False
        webapp.delete()
        return True

    deleted = await sync_to_async(_delete, thread_sensitive=True)()
    if not deleted:
        raise WebAppServiceError("The webapp slug provided does not exist in this conversation.")

    await publish_webapp_update(
        thread_id=getattr(thread, "id", None),
        slug=slug,
        task_id=task_id,
        channel_layer=channel_layer,
        reason="webapp_delete",
    )
    return {"slug": slug, "status": "deleted"}


def get_live_file_for_webapp(*, user, slug: str, requested_path: str | None = None) -> LiveWebAppFile | None:
    webapp = _get_webapp_sync(user, slug)
    if webapp is None:
        return None

    relative_path = str(requested_path or webapp.entry_path or "").strip()
    if not relative_path:
        return None
    if requested_path:
        normalized = posixpath.normpath(str(requested_path or "").strip().replace("\\", "/"))
        if normalized in {"", ".", "/", ".."} or normalized.startswith("../") or normalized.startswith("/"):
            return None
        relative_path = normalized

    if not _is_allowed_public_extension(relative_path):
        return None

    user_file = _get_live_user_file_sync(webapp, relative_path)
    if user_file is None:
        return None
    if relative_path == str(webapp.entry_path or "").strip() and relative_path.lower().endswith(".html"):
        content = async_to_sync(download_file_content)(user_file)
        if _looks_like_escaped_html(content):
            return None
    return LiveWebAppFile(
        webapp=webapp,
        user_file=user_file,
        relative_path=relative_path,
        mime_type=_guess_public_mime(user_file, relative_path),
    )


async def maybe_touch_impacted_webapps(
    *,
    thread,
    paths: list[str],
    moved_from: str | None = None,
    moved_to: str | None = None,
    deleted_roots: list[str] | None = None,
    task_id: int | str | None = None,
    channel_layer=None,
) -> list[dict[str, str]]:
    normalized_paths = [
        posixpath.normpath(str(path or "").strip() or "/")
        for path in paths
        if str(path or "").strip()
    ]
    moved_from_norm = posixpath.normpath(str(moved_from or "").strip()) if str(moved_from or "").strip() else None
    moved_to_norm = posixpath.normpath(str(moved_to or "").strip()) if str(moved_to or "").strip() else None
    deleted_root_paths = [
        posixpath.normpath(str(path or "").strip() or "/")
        for path in list(deleted_roots or [])
        if str(path or "").strip()
    ]

    def _touch() -> list[dict[str, str]]:
        impacted: list[WebApp] = []
        events: list[dict[str, str]] = []
        for webapp in WebApp.objects.filter(thread=thread).order_by("id"):
            if any(_path_impacts_webapp(webapp, path) for path in normalized_paths):
                impacted.append(webapp)

        if moved_from_norm and moved_to_norm:
            moved_prefix = f"{moved_from_norm.rstrip('/')}/"
            for webapp in WebApp.objects.filter(thread=thread).order_by("id"):
                source_root = str(webapp.source_root or "").strip()
                if source_root == moved_from_norm:
                    webapp.source_root = moved_to_norm
                    webapp.save(update_fields=["source_root", "updated_at"])
                    if webapp not in impacted:
                        impacted.append(webapp)
                elif source_root.startswith(moved_prefix):
                    suffix = source_root[len(moved_from_norm):]
                    webapp.source_root = posixpath.normpath(f"{moved_to_norm}{suffix}")
                    webapp.save(update_fields=["source_root", "updated_at"])
                    if webapp not in impacted:
                        impacted.append(webapp)

        if deleted_root_paths:
            retained: list[WebApp] = []
            for webapp in impacted:
                source_root = posixpath.normpath(str(webapp.source_root or "").strip() or "/")
                should_delete = any(
                    source_root == deleted_root
                    or source_root.startswith(f"{deleted_root.rstrip('/')}/")
                    for deleted_root in deleted_root_paths
                )
                if should_delete:
                    events.append({"slug": webapp.slug, "reason": "webapp_delete"})
                    webapp.delete()
                else:
                    retained.append(webapp)
            impacted = retained

        if not impacted:
            return events

        timestamp = timezone.now()
        for webapp in impacted:
            WebApp.objects.filter(id=webapp.id).update(updated_at=timestamp)
            events.append({"slug": webapp.slug, "reason": "webapp_update"})
        return events

    events = await sync_to_async(_touch, thread_sensitive=True)()
    for event in events:
        await publish_webapp_update(
            thread_id=getattr(thread, "id", None),
            slug=event["slug"],
            task_id=task_id,
            channel_layer=channel_layer,
            reason=event["reason"],
        )
    return events


def load_live_webapp_content(live_file: LiveWebAppFile) -> bytes:
    return async_to_sync(download_file_content)(live_file.user_file)
