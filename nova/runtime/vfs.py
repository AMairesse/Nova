from __future__ import annotations

import posixpath
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.core.exceptions import ValidationError
from django.db.models import Q

from nova.file_utils import batch_upload_files, download_file_content, upload_file_to_minio
from nova.message_attachments import (
    MESSAGE_ATTACHMENT_INBOX_ROOT,
    build_attachment_label,
    build_message_attachment_inbox_paths,
)
from nova.memory.service import (
    MEMORY_ROOT,
    archive_memory_path,
    find_memory_paths,
    is_memory_path,
    list_memory_dir_entries,
    memory_is_dir,
    memory_path_exists,
    mkdir_memory_dir,
    move_memory_path,
    read_memory_document,
    read_memory_text,
    write_memory_document,
)
from nova.models.Message import Message
from nova.models.UserFile import UserFile
from nova.webdav.service import (
    WEBDAV_MAX_RECURSIVE_PATHS,
    WEBDAV_VFS_ROOT,
    WebDAVMount,
    build_webdav_mounts,
    copy_path as webdav_copy_path,
    create_folder as webdav_create_folder,
    delete_path as webdav_delete_path,
    find_paths as find_webdav_paths,
    list_directory as list_webdav_directory,
    move_path as webdav_move_path,
    normalize_webdav_path,
    read_binary_file as read_webdav_binary_file,
    read_text_file as read_webdav_text_file,
    stat_path as stat_webdav_path,
    walk_paths as walk_webdav_paths,
    write_bytes as write_webdav_bytes,
)

from .constants import RUNTIME_STORAGE_ROOT


class VFSError(Exception):
    pass

INBOX_ROOT = MESSAGE_ATTACHMENT_INBOX_ROOT


@dataclass(slots=True)
class VFSFile:
    path: str
    user_file: UserFile | None
    mime_type: str
    size: int
    warnings: tuple[str, ...] = ()


def normalize_vfs_path(raw_path: str, *, cwd: str = "/") -> str:
    candidate = str(raw_path or "").strip()
    if not candidate:
        candidate = cwd or "/"
    if not candidate.startswith("/"):
        candidate = posixpath.join(cwd or "/", candidate)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


class VirtualFileSystem:
    def __init__(
        self,
        *,
        thread,
        user,
        agent_config,
        session_state: dict,
        skill_registry: dict[str, str],
        memory_enabled: bool = False,
        webdav_tools: list | None = None,
        source_message_id: int | None = None,
        source_message_inbox_enabled: bool = True,
        persistent_root_scope: str = UserFile.Scope.THREAD_SHARED,
        persistent_root_prefix: str | None = None,
        tmp_storage_prefix: str | None = None,
    ):
        self.thread = thread
        self.user = user
        self.agent_config = agent_config
        self.session_state = session_state
        self.skill_registry = dict(skill_registry or {})
        self.memory_enabled = bool(memory_enabled)
        self.webdav_mounts = build_webdav_mounts(list(webdav_tools or []))
        self._webdav_mounts_by_name = {mount.name: mount for mount in self.webdav_mounts}
        self.source_message_id = source_message_id
        self.source_message_inbox_enabled = bool(source_message_inbox_enabled)
        self.persistent_root_scope = str(persistent_root_scope or UserFile.Scope.THREAD_SHARED)
        self.persistent_root_prefix = (
            str(persistent_root_prefix or "").rstrip("/") if persistent_root_prefix else None
        )
        self.tmp_storage_prefix = (
            str(tmp_storage_prefix or self._default_tmp_storage_prefix()).rstrip("/")
        )

    def _default_tmp_storage_prefix(self) -> str:
        return f"{RUNTIME_STORAGE_ROOT}/{int(self.agent_config.id)}/tmp"

    @staticmethod
    def _is_reserved_memory_path(path: str) -> bool:
        normalized = normalize_vfs_path(path, cwd="/")
        return normalized == MEMORY_ROOT or normalized.startswith(f"{MEMORY_ROOT}/")

    @staticmethod
    def _is_reserved_webdav_path(path: str) -> bool:
        normalized = normalize_vfs_path(path, cwd="/")
        return normalized == WEBDAV_VFS_ROOT or normalized.startswith(f"{WEBDAV_VFS_ROOT}/")

    def _is_memory_enabled_path(self, path: str) -> bool:
        return self.memory_enabled and is_memory_path(path)

    @staticmethod
    def _is_inbox_path(path: str) -> bool:
        normalized = normalize_vfs_path(path, cwd="/")
        return normalized == INBOX_ROOT or normalized.startswith(f"{INBOX_ROOT}/")

    def _has_source_message_inbox(self) -> bool:
        return self.source_message_inbox_enabled and self.source_message_id is not None

    def _has_inbox_dir(self) -> bool:
        return self._has_source_message_inbox() or INBOX_ROOT in self._get_session_dirs()

    @staticmethod
    def _inbox_filename_for_user_file(user_file: UserFile) -> str:
        basename = build_attachment_label(user_file, fallback="")
        if basename:
            return basename
        file_id = getattr(user_file, "id", None)
        if file_id is not None:
            return f"attachment-{file_id}"
        return "attachment"

    @classmethod
    def build_source_message_inbox_path(cls, user_file: UserFile) -> str:
        aliases = build_message_attachment_inbox_paths([user_file])
        path = aliases.get(getattr(user_file, "id", None))
        return normalize_vfs_path(path or f"{INBOX_ROOT}/{cls._inbox_filename_for_user_file(user_file)}", cwd="/")

    @classmethod
    def build_source_message_inbox_paths(
        cls,
        user_files: list[UserFile],
    ) -> dict[int, str]:
        return {
            file_id: normalize_vfs_path(path, cwd="/")
            for file_id, path in build_message_attachment_inbox_paths(user_files).items()
        }

    def _resolve_webdav_path(self, path: str) -> tuple[str, WebDAVMount | None, str | None]:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized == WEBDAV_VFS_ROOT:
            return "root", None, "/"
        if not normalized.startswith(f"{WEBDAV_VFS_ROOT}/"):
            return "none", None, None
        relative = normalized[len(f"{WEBDAV_VFS_ROOT}/"):]
        parts = [part for part in relative.split("/") if part]
        if not parts:
            return "root", None, "/"
        mount = self._webdav_mounts_by_name.get(parts[0])
        if mount is None:
            return "unknown", None, None
        if len(parts) == 1:
            return "mount", mount, "/"
        remote_path = normalize_webdav_path("/" + "/".join(parts[1:]))
        return "mount", mount, remote_path

    @property
    def cwd(self) -> str:
        return normalize_vfs_path(self.session_state.get("cwd", "/"), cwd="/")

    def set_cwd(self, path: str) -> None:
        self.session_state["cwd"] = normalize_vfs_path(path, cwd=self.cwd)

    def remember_command(self, command: str) -> None:
        history = [str(item) for item in list(self.session_state.get("history") or []) if str(item).strip()]
        history.append(str(command))
        self.session_state["history"] = history[-50:]

    def _get_session_dirs(self) -> set[str]:
        directories = set()
        for path in list(self.session_state.get("directories") or []):
            normalized = normalize_vfs_path(path, cwd="/")
            if normalized != "/":
                directories.add(normalized)
        return directories

    def _set_session_dirs(self, dirs: set[str]) -> None:
        self.session_state["directories"] = sorted(
            directory for directory in dirs if directory and directory != "/"
        )

    def _storage_path_for_vfs_path(
        self,
        path: str,
        *,
        allow_inbox_write: bool = False,
    ) -> tuple[str, str]:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized == "/skills" or normalized.startswith("/skills/"):
            raise VFSError("Writing into /skills is not supported.")
        if self._is_inbox_path(normalized) and not allow_inbox_write:
            raise VFSError("Writing into /inbox is not supported. Copy files elsewhere first.")
        if self._is_reserved_memory_path(normalized):
            if not self.memory_enabled:
                raise VFSError("Memory is not enabled for this agent.")
            if not is_memory_path(normalized):
                raise VFSError("Invalid memory path.")
        if self._is_reserved_webdav_path(normalized):
            if not self.webdav_mounts:
                raise VFSError("WebDAV is not enabled for this agent.")
            raise VFSError("Direct storage mapping is not available for /webdav paths.")
        if normalized == "/tmp" or normalized.startswith("/tmp/"):
            suffix = normalized[len("/tmp"):] or "/"
            return UserFile.Scope.MESSAGE_ATTACHMENT, f"{self.tmp_storage_prefix}{suffix}"
        if self.persistent_root_scope == UserFile.Scope.THREAD_SHARED:
            return UserFile.Scope.THREAD_SHARED, normalized
        if not self.persistent_root_prefix:
            raise VFSError("Persistent root prefix is not configured.")
        return UserFile.Scope.MESSAGE_ATTACHMENT, f"{self.persistent_root_prefix}{normalized}"

    def _root_vfs_path_for_user_file(self, original_path: str) -> str | None:
        raw_path = str(original_path or "").strip()
        if self.persistent_root_scope == UserFile.Scope.THREAD_SHARED:
            normalized = normalize_vfs_path(raw_path or "/", cwd="/")
            if self._is_reserved_memory_path(normalized) or self._is_reserved_webdav_path(normalized):
                return None
            return normalized
        if self.persistent_root_prefix and raw_path.startswith(self.persistent_root_prefix):
            suffix = raw_path[len(self.persistent_root_prefix):] or "/"
            normalized = normalize_vfs_path(suffix, cwd="/")
            if self._is_reserved_memory_path(normalized) or self._is_reserved_webdav_path(normalized):
                return None
            return normalized
        return None

    def _vfs_path_for_user_file(
        self,
        user_file: UserFile,
        *,
        source_message_aliases: dict[int, str] | None = None,
    ) -> str | None:
        original_path = str(user_file.original_filename or "").strip()
        if (
            self._has_source_message_inbox()
            and user_file.scope == UserFile.Scope.MESSAGE_ATTACHMENT
            and getattr(user_file, "source_message_id", None) == self.source_message_id
        ):
            file_id = getattr(user_file, "id", None)
            if file_id is not None and source_message_aliases and file_id in source_message_aliases:
                return source_message_aliases[file_id]
            return self.build_source_message_inbox_path(user_file)
        if user_file.scope == UserFile.Scope.MESSAGE_ATTACHMENT:
            if original_path.startswith(self.tmp_storage_prefix):
                suffix = original_path[len(self.tmp_storage_prefix):] or ""
                return normalize_vfs_path(f"/tmp{suffix}", cwd="/")

        if user_file.scope == self.persistent_root_scope:
            return self._root_vfs_path_for_user_file(original_path)
        return None

    async def _load_real_files(self) -> list[VFSFile]:
        def _load():
            query = Q(scope=UserFile.Scope.MESSAGE_ATTACHMENT, original_filename__startswith=self.tmp_storage_prefix)
            if self._has_source_message_inbox():
                query |= Q(
                    scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                    source_message_id=self.source_message_id,
                )
            if self.persistent_root_scope == UserFile.Scope.THREAD_SHARED:
                query |= Q(scope=UserFile.Scope.THREAD_SHARED)
            elif self.persistent_root_prefix:
                query |= Q(
                    scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                    original_filename__startswith=self.persistent_root_prefix,
                )
            return list(
                UserFile.objects.filter(
                    user=self.user,
                    thread=self.thread,
                ).filter(query)
            )

        user_files = await sync_to_async(_load, thread_sensitive=True)()
        source_message_aliases: dict[int, str] = {}
        if self._has_source_message_inbox():
            source_message_aliases = self.build_source_message_inbox_paths([
                user_file
                for user_file in user_files
                if (
                    user_file.scope == UserFile.Scope.MESSAGE_ATTACHMENT
                    and getattr(user_file, "source_message_id", None) == self.source_message_id
                )
            ])
        by_path: dict[str, VFSFile] = {}
        for user_file in user_files:
            vfs_path = self._vfs_path_for_user_file(
                user_file,
                source_message_aliases=source_message_aliases,
            )
            if not vfs_path:
                continue
            candidate = VFSFile(
                path=normalize_vfs_path(vfs_path, cwd="/"),
                user_file=user_file,
                mime_type=str(user_file.mime_type or "application/octet-stream"),
                size=int(user_file.size or 0),
            )
            existing = by_path.get(candidate.path)
            if existing is None:
                by_path[candidate.path] = candidate
                continue
            existing_scope = getattr(existing.user_file, "scope", None)
            candidate_scope = getattr(candidate.user_file, "scope", None)
            if existing_scope != UserFile.Scope.THREAD_SHARED and candidate_scope == UserFile.Scope.THREAD_SHARED:
                by_path[candidate.path] = candidate
            elif getattr(candidate.user_file, "id", 0) > getattr(existing.user_file, "id", 0):
                by_path[candidate.path] = candidate
        return list(by_path.values())

    async def path_exists(self, path: str) -> bool:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized in {"/", "/skills", "/tmp"}:
            return True
        if normalized == INBOX_ROOT:
            return self._has_inbox_dir()
        if normalized == WEBDAV_VFS_ROOT:
            return bool(self.webdav_mounts)
        if self._is_reserved_memory_path(normalized) and not self.memory_enabled:
            return False
        if self._is_reserved_memory_path(normalized) and not is_memory_path(normalized):
            return False
        if self._is_reserved_webdav_path(normalized) and not self.webdav_mounts:
            return False
        if self._is_memory_enabled_path(normalized):
            return await memory_path_exists(user=self.user, path=normalized)
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "unknown":
            return False
        if webdav_kind == "mount":
            if webdav_path == "/":
                return True
            metadata = await stat_webdav_path(webdav_mount.tool, webdav_path)
            return bool(metadata.get("exists"))
        if normalized.startswith("/skills/"):
            skill_name = posixpath.basename(normalized)
            return skill_name in self.skill_registry
        if normalized in self._get_session_dirs():
            return True
        for item in await self._load_real_files():
            if item.path == normalized:
                return True
            if item.path.startswith(f"{normalized.rstrip('/')}/"):
                return True
        return False

    async def is_dir(self, path: str) -> bool:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized in {"/", "/skills", "/tmp"}:
            return True
        if normalized == INBOX_ROOT:
            return self._has_inbox_dir()
        if normalized == WEBDAV_VFS_ROOT:
            return bool(self.webdav_mounts)
        if self._is_reserved_memory_path(normalized) and not self.memory_enabled:
            return False
        if self._is_reserved_memory_path(normalized) and not is_memory_path(normalized):
            return False
        if self._is_reserved_webdav_path(normalized) and not self.webdav_mounts:
            return False
        if self._is_memory_enabled_path(normalized):
            return await memory_is_dir(user=self.user, path=normalized)
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "unknown":
            return False
        if webdav_kind == "mount":
            if webdav_path == "/":
                return True
            metadata = await stat_webdav_path(webdav_mount.tool, webdav_path)
            return metadata.get("exists") and metadata.get("type") == "directory"
        if normalized in self._get_session_dirs():
            return True
        for item in await self._load_real_files():
            if item.path.startswith(f"{normalized.rstrip('/')}/"):
                return True
        return False

    async def list_dir(self, path: str | None = None) -> list[dict]:
        normalized = normalize_vfs_path(path or self.cwd, cwd=self.cwd)
        if normalized == "/skills":
            return [
                {"name": name, "path": f"/skills/{name}", "type": "file"}
                for name in sorted(self.skill_registry.keys())
            ]
        if normalized == WEBDAV_VFS_ROOT:
            return [
                {"name": mount.name, "path": f"{WEBDAV_VFS_ROOT}/{mount.name}", "type": "dir"}
                for mount in self.webdav_mounts
            ]
        if self._is_memory_enabled_path(normalized):
            return await list_memory_dir_entries(user=self.user, path=normalized)
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "mount":
            return [
                {
                    "name": entry["name"],
                    "path": (
                        f"{WEBDAV_VFS_ROOT}/{webdav_mount.name}"
                        if entry["path"] == "/"
                        else f"{WEBDAV_VFS_ROOT}/{webdav_mount.name}{entry['path']}"
                    ),
                    "type": "dir" if entry["type"] == "directory" else "file",
                    "mime_type": entry.get("mime_type"),
                    "size": entry.get("size"),
                }
                for entry in await list_webdav_directory(webdav_mount.tool, webdav_path)
            ]

        entries: dict[str, dict] = {}
        if normalized == "/":
            entries["skills"] = {"name": "skills", "path": "/skills", "type": "dir"}
            if self._has_inbox_dir():
                entries["inbox"] = {"name": "inbox", "path": INBOX_ROOT, "type": "dir"}
            entries["tmp"] = {"name": "tmp", "path": "/tmp", "type": "dir"}
            if self.webdav_mounts:
                entries["webdav"] = {"name": "webdav", "path": WEBDAV_VFS_ROOT, "type": "dir"}
            if self.memory_enabled:
                entries["memory"] = {"name": "memory", "path": MEMORY_ROOT, "type": "dir"}

        all_files = await self._load_real_files()
        session_dirs = self._get_session_dirs()

        for directory in sorted(session_dirs):
            if directory == normalized:
                continue
            if posixpath.dirname(directory) == normalized:
                name = posixpath.basename(directory)
                entries[name] = {"name": name, "path": directory, "type": "dir"}

        prefix = normalized.rstrip("/") + "/"
        for item in all_files:
            if item.path == normalized:
                name = posixpath.basename(item.path)
                entries[name] = {
                    "name": name,
                    "path": item.path,
                    "type": "file",
                    "mime_type": item.mime_type,
                    "size": item.size,
                }
                continue
            if not item.path.startswith(prefix):
                continue
            relative = item.path[len(prefix):]
            child = relative.split("/", 1)[0]
            child_path = f"{normalized.rstrip('/')}/{child}" if normalized != "/" else f"/{child}"
            if "/" in relative:
                entries.setdefault(child, {"name": child, "path": child_path, "type": "dir"})
            else:
                entries[child] = {
                    "name": child,
                    "path": child_path,
                    "type": "file",
                    "mime_type": item.mime_type,
                    "size": item.size,
                }

        return [entries[key] for key in sorted(entries.keys())]

    async def get_real_file(self, path: str) -> VFSFile | None:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        for item in await self._load_real_files():
            if item.path == normalized:
                return item
        return None

    async def read_text(self, path: str) -> str:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized.startswith("/skills/"):
            skill_name = posixpath.basename(normalized)
            if skill_name not in self.skill_registry:
                raise VFSError(f"Skill file not found: {normalized}")
            return self.skill_registry[skill_name]
        if self._is_memory_enabled_path(normalized):
            try:
                return await read_memory_text(user=self.user, path=normalized)
            except ValidationError as exc:
                raise VFSError(str(exc)) from exc
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "mount":
            try:
                return await read_webdav_text_file(webdav_mount.tool, webdav_path)
            except ValueError as exc:
                raise VFSError(str(exc)) from exc

        item = await self.get_real_file(normalized)
        if item is None or item.user_file is None:
            raise VFSError(f"File not found: {normalized}")

        content = await download_file_content(item.user_file)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VFSError(
                f"Binary file cannot be displayed as text: {normalized} ({item.mime_type}, {item.size} bytes)"
            ) from exc

    async def read_bytes(self, path: str) -> tuple[bytes, str]:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if self._is_memory_enabled_path(normalized):
            try:
                entry = await read_memory_document(user=self.user, path=normalized)
                text = await read_memory_text(user=self.user, path=normalized)
            except ValidationError as exc:
                raise VFSError(str(exc)) from exc
            return text.encode("utf-8"), entry.mime_type
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "mount":
            try:
                payload = await read_webdav_binary_file(webdav_mount.tool, webdav_path)
            except ValueError as exc:
                raise VFSError(str(exc)) from exc
            return payload["content"], payload["mime_type"]
        item = await self.get_real_file(normalized)
        if item is None or item.user_file is None:
            raise VFSError(f"File not found: {normalized}")
        return await download_file_content(item.user_file), item.mime_type

    async def mkdir(self, path: str) -> str:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized.startswith("/skills"):
            raise VFSError("mkdir is not supported inside /skills.")
        if normalized == INBOX_ROOT and self._has_inbox_dir():
            return normalized
        if self._is_inbox_path(normalized):
            raise VFSError("mkdir is not supported inside /inbox.")
        if self._is_reserved_memory_path(normalized):
            if not self.memory_enabled:
                raise VFSError("Memory is not enabled for this agent.")
            if not is_memory_path(normalized):
                raise VFSError("Invalid memory path.")
        if self._is_memory_enabled_path(normalized):
            try:
                return await mkdir_memory_dir(user=self.user, path=normalized)
            except ValidationError as exc:
                raise VFSError(str(exc)) from exc
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "mount":
            if webdav_path == "/":
                return normalized
            try:
                await webdav_create_folder(webdav_mount.tool, webdav_path, recursive=False)
            except ValueError as exc:
                raise VFSError(str(exc)) from exc
            return normalized
        if self._is_reserved_webdav_path(normalized):
            if not self.webdav_mounts:
                raise VFSError("WebDAV is not enabled for this agent.")
            raise VFSError("Invalid WebDAV path.")
        if normalized in {"/", "/tmp"}:
            return normalized
        existing_file = await self.get_real_file(normalized)
        if existing_file is not None:
            raise VFSError(f"Cannot create directory over file: {normalized}")
        if await self.is_dir(normalized):
            return normalized
        dirs = self._get_session_dirs()
        dirs.add(normalized)
        self._set_session_dirs(dirs)
        return normalized

    async def write_file(
        self,
        path: str,
        content: bytes,
        *,
        mime_type: str = "application/octet-stream",
        overwrite: bool = True,
        allow_inbox_write: bool = False,
    ) -> VFSFile:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if self._is_memory_enabled_path(normalized):
            if not overwrite and await memory_path_exists(user=self.user, path=normalized):
                raise VFSError(f"File already exists: {normalized}")
            try:
                decoded = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise VFSError("Memory files must be valid UTF-8 text.") from exc
            source_message = None
            if self.source_message_id is not None:
                def _load_source_message():
                    return Message.objects.filter(
                        id=self.source_message_id,
                        user=self.user,
                        thread=self.thread,
                    ).first()

                source_message = await sync_to_async(_load_source_message, thread_sensitive=True)()
            try:
                entry = await write_memory_document(
                    user=self.user,
                    path=normalized,
                    text=decoded,
                    source_thread=self.thread,
                    source_message=source_message,
                )
            except ValidationError as exc:
                raise VFSError(str(exc)) from exc
            return VFSFile(
                path=entry.path,
                user_file=None,
                mime_type=entry.mime_type,
                size=entry.size,
                warnings=entry.warnings,
            )
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "mount":
            try:
                result = await write_webdav_bytes(
                    webdav_mount.tool,
                    webdav_path,
                    content,
                    mime_type=mime_type,
                    overwrite=overwrite,
                )
            except ValueError as exc:
                raise VFSError(str(exc)) from exc
            return VFSFile(
                path=normalized,
                user_file=None,
                mime_type=result["mime_type"],
                size=result["size"],
            )
        if self._is_reserved_webdav_path(normalized):
            if not self.webdav_mounts:
                raise VFSError("WebDAV is not enabled for this agent.")
            raise VFSError("Invalid WebDAV path.")
        scope, storage_path = self._storage_path_for_vfs_path(
            normalized,
            allow_inbox_write=allow_inbox_write,
        )
        existing = await self.get_real_file(normalized)
        if existing and existing.user_file is not None:
            if not overwrite:
                raise VFSError(f"File already exists: {normalized}")
            await sync_to_async(existing.user_file.delete, thread_sensitive=True)()

        if len(content) == 0:
            key = await upload_file_to_minio(content, storage_path, mime_type, self.thread, self.user)

            def _create_empty_file():
                return UserFile.objects.create(
                    user=self.user,
                    thread=self.thread,
                    original_filename=storage_path,
                    mime_type=mime_type,
                    size=0,
                    key=key,
                    scope=scope,
                )

            user_file = await sync_to_async(_create_empty_file, thread_sensitive=True)()
            return VFSFile(
                path=normalized,
                user_file=user_file,
                mime_type=str(user_file.mime_type or mime_type),
                size=0,
            )

        created, errors = await batch_upload_files(
            self.thread,
            self.user,
            [{"path": storage_path, "content": content, "mime_type": mime_type}],
            scope=scope,
        )
        if errors and not created:
            raise VFSError("; ".join(errors))
        created_id = created[0]["id"]

        def _load():
            return UserFile.objects.get(id=created_id, user=self.user, thread=self.thread)

        user_file = await sync_to_async(_load, thread_sensitive=True)()
        return VFSFile(
            path=normalized,
            user_file=user_file,
            mime_type=str(user_file.mime_type or mime_type),
            size=int(user_file.size or len(content)),
        )

    async def remove(self, path: str) -> None:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized in {"/", "/skills", "/tmp"}:
            raise VFSError(f"Cannot remove protected path: {normalized}")
        if self._is_inbox_path(normalized):
            raise VFSError("Removing files from /inbox is not supported.")
        if self._is_memory_enabled_path(normalized):
            try:
                await archive_memory_path(user=self.user, path=normalized)
                return
            except ValidationError as exc:
                raise VFSError(str(exc)) from exc
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized)
        if webdav_kind == "mount":
            if webdav_path == "/":
                raise VFSError(f"Cannot remove protected path: {normalized}")
            try:
                await webdav_delete_path(webdav_mount.tool, webdav_path)
                return
            except ValueError as exc:
                raise VFSError(str(exc)) from exc
        if self._is_reserved_webdav_path(normalized):
            if not self.webdav_mounts:
                raise VFSError("WebDAV is not enabled for this agent.")
            raise VFSError("Path not found: {normalized}".format(normalized=normalized))

        item = await self.get_real_file(normalized)
        if item and item.user_file is not None:
            await sync_to_async(item.user_file.delete, thread_sensitive=True)()
            return

        if await self.is_dir(normalized):
            children = await self.list_dir(normalized)
            if children:
                raise VFSError(f"Directory not empty: {normalized}")
            dirs = self._get_session_dirs()
            if normalized in dirs:
                dirs.remove(normalized)
                self._set_session_dirs(dirs)
                return

        raise VFSError(f"Path not found: {normalized}")

    async def resolve_output_path(self, destination: str, *, source_name: str | None = None) -> str:
        raw_destination = str(destination or "").strip()
        normalized_destination = normalize_vfs_path(raw_destination, cwd=self.cwd)
        if normalized_destination.startswith("/skills"):
            raise VFSError("Writing into /skills is not supported.")
        if raw_destination.endswith("/") and not await self.path_exists(normalized_destination):
            raise VFSError(f"Directory not found: {normalized_destination}")
        if await self.path_exists(normalized_destination) and await self.is_dir(normalized_destination):
            if not source_name:
                raise VFSError(f"Destination is a directory: {normalized_destination}")
            return normalize_vfs_path(
                posixpath.join(normalized_destination, str(source_name or "").strip()),
                cwd="/",
            )
        return normalized_destination

    async def copy(self, source: str, destination: str) -> VFSFile:
        normalized_source = normalize_vfs_path(source, cwd=self.cwd)
        source_name = posixpath.basename(normalized_source) or "file"
        resolved_destination = await self.resolve_output_path(destination, source_name=source_name)
        source_webdav_kind, source_webdav_mount, source_webdav_path = self._resolve_webdav_path(normalized_source)
        destination_webdav_kind, destination_webdav_mount, destination_webdav_path = self._resolve_webdav_path(resolved_destination)
        source_is_webdav = source_webdav_kind == "mount"
        destination_is_webdav = destination_webdav_kind == "mount"

        if source_is_webdav and destination_is_webdav and source_webdav_mount.name == destination_webdav_mount.name:
            source_metadata = await stat_webdav_path(source_webdav_mount.tool, source_webdav_path)
            if source_metadata.get("type") == "directory":
                raise VFSError("Copying WebDAV directories within the same mount is not supported.")
            try:
                result = await webdav_copy_path(
                    source_webdav_mount.tool,
                    source_webdav_path,
                    destination_webdav_path,
                    overwrite=True,
                )
            except ValueError as exc:
                raise VFSError(str(exc)) from exc
            copied_path = (
                f"{WEBDAV_VFS_ROOT}/{source_webdav_mount.name}"
                if result["path"] == "/"
                else f"{WEBDAV_VFS_ROOT}/{source_webdav_mount.name}{result['path']}"
            )
            return VFSFile(
                path=copied_path,
                user_file=None,
                mime_type=str(source_metadata.get("mime_type") or "application/octet-stream"),
                size=int(source_metadata.get("size") or 0),
            )

        if (source_is_webdav or destination_is_webdav) and await self.is_dir(normalized_source):
            raise VFSError("Copying directories across WebDAV boundaries is not supported.")

        if normalized_source.startswith("/skills/"):
            content = (await self.read_text(normalized_source)).encode("utf-8")
            return await self.write_file(resolved_destination, content, mime_type="text/markdown")
        content, mime_type = await self.read_bytes(normalized_source)
        return await self.write_file(resolved_destination, content, mime_type=mime_type)

    async def move(self, source: str, destination: str) -> str:
        normalized_source = normalize_vfs_path(source, cwd=self.cwd)
        if self._is_inbox_path(normalized_source):
            raise VFSError("Moving files from /inbox is not supported. Use cp instead.")
        source_name = posixpath.basename(normalized_source) or "file"
        resolved_destination = await self.resolve_output_path(destination, source_name=source_name)
        source_webdav_kind, source_webdav_mount, source_webdav_path = self._resolve_webdav_path(normalized_source)
        destination_webdav_kind, destination_webdav_mount, destination_webdav_path = self._resolve_webdav_path(resolved_destination)
        source_is_webdav = source_webdav_kind == "mount"
        destination_is_webdav = destination_webdav_kind == "mount"

        if source_is_webdav and destination_is_webdav and source_webdav_mount.name == destination_webdav_mount.name:
            try:
                result = await webdav_move_path(
                    source_webdav_mount.tool,
                    source_webdav_path,
                    destination_webdav_path,
                    overwrite=True,
                )
            except ValueError as exc:
                raise VFSError(str(exc)) from exc
            return f"{WEBDAV_VFS_ROOT}/{source_webdav_mount.name}{result['path']}"

        if (source_is_webdav or destination_is_webdav) and await self.is_dir(normalized_source):
            raise VFSError("Moving directories across WebDAV boundaries is not supported.")

        source_is_memory = self._is_memory_enabled_path(normalized_source)
        destination_is_memory = self._is_memory_enabled_path(resolved_destination)
        if source_is_memory and destination_is_memory:
            try:
                return await move_memory_path(
                    user=self.user,
                    source_path=normalized_source,
                    destination_path=resolved_destination,
                )
            except ValidationError as exc:
                raise VFSError(str(exc)) from exc
        if source_is_memory or destination_is_memory:
            await self.copy(normalized_source, resolved_destination)
            await self.remove(normalized_source)
            return resolved_destination
        if source_is_webdav or destination_is_webdav:
            await self.copy(normalized_source, resolved_destination)
            await self.remove(normalized_source)
            return resolved_destination
        if await self.is_dir(normalized_source):
            if normalized_source in {"/", "/skills", "/tmp"}:
                raise VFSError(f"Cannot move protected directory: {normalized_source}")
            if resolved_destination == normalized_source:
                return resolved_destination
            source_prefix = f"{normalized_source.rstrip('/')}/"
            if resolved_destination.startswith(source_prefix):
                raise VFSError("Cannot move a directory inside itself.")
            if await self.path_exists(resolved_destination) and not await self.is_dir(resolved_destination):
                raise VFSError(f"Cannot move directory over file: {resolved_destination}")

            all_files = await self._load_real_files()
            moved_file_paths = {
                item.path for item in all_files if item.path.startswith(source_prefix)
            }
            file_moves: list[tuple[VFSFile, str, str, str]] = []
            for item in all_files:
                if not item.path.startswith(source_prefix):
                    continue
                suffix = item.path[len(normalized_source):]
                destination_path = normalize_vfs_path(f"{resolved_destination}{suffix}", cwd="/")
                for candidate in all_files:
                    if candidate.path == destination_path and candidate.path not in moved_file_paths:
                        raise VFSError(f"Destination already exists: {destination_path}")
                actual_src_scope = str(item.user_file.scope or "") if item.user_file is not None else ""
                dst_scope, dst_storage = self._storage_path_for_vfs_path(destination_path)
                if actual_src_scope != dst_scope:
                    raise VFSError("Moving directories across storage boundaries is not supported.")
                file_moves.append((item, destination_path, dst_storage, dst_scope))

            dirs = self._get_session_dirs()
            updated_dirs: set[str] = set()
            for directory in dirs:
                if directory == normalized_source or directory.startswith(source_prefix):
                    suffix = directory[len(normalized_source):]
                    updated_dirs.add(normalize_vfs_path(f"{resolved_destination}{suffix}", cwd="/"))
                else:
                    updated_dirs.add(directory)

            def _save_directory_move():
                for file_item, _destination_path, dst_storage, dst_scope in file_moves:
                    file_item.user_file.original_filename = dst_storage
                    file_item.user_file.scope = dst_scope
                    file_item.user_file.save(update_fields=["original_filename", "scope", "updated_at"])

            await sync_to_async(_save_directory_move, thread_sensitive=True)()
            self._set_session_dirs(updated_dirs)
            return resolved_destination
        item = await self.get_real_file(normalized_source)
        if item is None or item.user_file is None:
            raise VFSError(f"File not found: {normalized_source}")
        if normalized_source == resolved_destination:
            return resolved_destination

        destination_existing = await self.get_real_file(resolved_destination)
        if destination_existing and destination_existing.user_file is not None:
            await sync_to_async(destination_existing.user_file.delete, thread_sensitive=True)()

        actual_src_scope = str(item.user_file.scope or "")
        dst_scope, dst_storage = self._storage_path_for_vfs_path(resolved_destination)
        if actual_src_scope != dst_scope:
            await self.copy(normalized_source, resolved_destination)
            await self.remove(normalized_source)
            return resolved_destination

        def _save():
            item.user_file.original_filename = dst_storage
            item.user_file.scope = dst_scope
            item.user_file.save(update_fields=["original_filename", "scope", "updated_at"])

        await sync_to_async(_save, thread_sensitive=True)()
        return resolved_destination

    async def find(self, start_path: str, term: str = "") -> list[str]:
        normalized_start = normalize_vfs_path(start_path, cwd=self.cwd)
        matches: list[str] = []
        if normalized_start == WEBDAV_VFS_ROOT:
            lowered_term = str(term or "").lower()
            remaining = WEBDAV_MAX_RECURSIVE_PATHS
            for mount in self.webdav_mounts:
                mount_root = f"{WEBDAV_VFS_ROOT}/{mount.name}"
                if not lowered_term or lowered_term in mount.name.lower():
                    matches.append(mount_root)
                mount_matches, examined = await walk_webdav_paths(
                    mount.tool,
                    start_path="/",
                    term=term,
                    limit=remaining,
                )
                remaining -= examined
                matches.extend(
                    mount_root if path == "/" else f"{WEBDAV_VFS_ROOT}/{mount.name}{path}"
                    for path in mount_matches
                )
            return sorted(set(matches))
        if self._is_memory_enabled_path(normalized_start):
            try:
                return await find_memory_paths(user=self.user, start_path=normalized_start, term=term)
            except ValidationError as exc:
                raise VFSError(str(exc)) from exc
        webdav_kind, webdav_mount, webdav_path = self._resolve_webdav_path(normalized_start)
        if webdav_kind == "mount":
            try:
                results = await find_webdav_paths(
                    webdav_mount.tool,
                    start_path=webdav_path,
                    term=term,
                    limit=WEBDAV_MAX_RECURSIVE_PATHS,
                )
            except ValueError as exc:
                raise VFSError(str(exc)) from exc
            return [
                f"{WEBDAV_VFS_ROOT}/{webdav_mount.name}"
                if path == "/"
                else f"{WEBDAV_VFS_ROOT}/{webdav_mount.name}{path}"
                for path in results
            ]

        if normalized_start in {"/skills", "/"}:
            for name in sorted(self.skill_registry.keys()):
                full_path = f"/skills/{name}"
                if normalized_start == "/skills" or normalized_start == "/":
                    if not term or term.lower() in name.lower():
                        matches.append(full_path)

        prefix = normalized_start.rstrip("/") + "/"
        for item in await self._load_real_files():
            if item.path == normalized_start or item.path.startswith(prefix):
                if not term or term.lower() in posixpath.basename(item.path).lower():
                    matches.append(item.path)

        for directory in sorted(self._get_session_dirs()):
            if directory == normalized_start or directory.startswith(prefix):
                if not term or term.lower() in posixpath.basename(directory).lower():
                    matches.append(directory)

        if normalized_start == "/" and self.memory_enabled:
            memory_matches = await find_memory_paths(user=self.user, start_path=MEMORY_ROOT, term=term)
            matches.extend(memory_matches)
        if normalized_start == "/" and self.webdav_mounts:
            matches.append(WEBDAV_VFS_ROOT)

        return sorted(set(matches))

    async def suggest_inbox_path(self, missing_path: str) -> str | None:
        basename = posixpath.basename(str(missing_path or "").strip())
        if not basename:
            return None
        lowered = basename.lower()
        for item in await self._load_real_files():
            if item.path.startswith(f"{INBOX_ROOT}/") and posixpath.basename(item.path).lower() == lowered:
                return item.path
        return None

    async def snapshot_persistent_files(self) -> dict[str, tuple[int | None, int, str]]:
        snapshot: dict[str, tuple[int | None, int, str]] = {}
        for item in await self._load_real_files():
            if item.path == "/tmp" or item.path.startswith("/tmp/"):
                continue
            snapshot[item.path] = (
                getattr(item.user_file, "id", None),
                item.size,
                item.mime_type,
            )
        return snapshot

    async def snapshot_visible_files(
        self,
        *,
        include_inbox: bool = False,
    ) -> dict[str, tuple[int | None, int, str]]:
        snapshot: dict[str, tuple[int | None, int, str]] = {}
        for item in await self._load_real_files():
            if not include_inbox and (item.path == INBOX_ROOT or item.path.startswith(f"{INBOX_ROOT}/")):
                continue
            snapshot[item.path] = (
                getattr(item.user_file, "id", None),
                item.size,
                item.mime_type,
            )
        return snapshot
