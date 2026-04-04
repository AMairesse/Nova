from __future__ import annotations

import posixpath
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.db.models import Q

from nova.file_utils import batch_upload_files, download_file_content, upload_file_to_minio
from nova.models.UserFile import UserFile

from .constants import RUNTIME_STORAGE_ROOT


class VFSError(Exception):
    pass


@dataclass(slots=True)
class VFSFile:
    path: str
    user_file: UserFile | None
    mime_type: str
    size: int


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
        persistent_root_scope: str = UserFile.Scope.THREAD_SHARED,
        persistent_root_prefix: str | None = None,
        tmp_storage_prefix: str | None = None,
        legacy_workspace_storage_prefix: str | None = None,
    ):
        self.thread = thread
        self.user = user
        self.agent_config = agent_config
        self.session_state = session_state
        self.skill_registry = dict(skill_registry or {})
        self.persistent_root_scope = str(persistent_root_scope or UserFile.Scope.THREAD_SHARED)
        self.persistent_root_prefix = (
            str(persistent_root_prefix or "").rstrip("/") if persistent_root_prefix else None
        )
        self.tmp_storage_prefix = (
            str(tmp_storage_prefix or self._default_tmp_storage_prefix()).rstrip("/")
        )
        self.legacy_workspace_storage_prefix = (
            str(legacy_workspace_storage_prefix or "").rstrip("/")
            if legacy_workspace_storage_prefix
            else None
        )

    def _default_tmp_storage_prefix(self) -> str:
        return f"{RUNTIME_STORAGE_ROOT}/{int(self.agent_config.id)}/tmp"

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

    def _storage_path_for_vfs_path(self, path: str) -> tuple[str, str]:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized == "/skills" or normalized.startswith("/skills/"):
            raise VFSError("Writing into /skills is not supported.")
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
            return normalize_vfs_path(raw_path or "/", cwd="/")
        if self.persistent_root_prefix and raw_path.startswith(self.persistent_root_prefix):
            suffix = raw_path[len(self.persistent_root_prefix):] or "/"
            return normalize_vfs_path(suffix, cwd="/")
        return None

    def _vfs_path_for_user_file(self, user_file: UserFile) -> str | None:
        original_path = str(user_file.original_filename or "").strip()
        if user_file.scope == UserFile.Scope.MESSAGE_ATTACHMENT:
            if original_path.startswith(self.tmp_storage_prefix):
                suffix = original_path[len(self.tmp_storage_prefix):] or ""
                return normalize_vfs_path(f"/tmp{suffix}", cwd="/")
            if self.legacy_workspace_storage_prefix and original_path.startswith(self.legacy_workspace_storage_prefix):
                suffix = original_path[len(self.legacy_workspace_storage_prefix):] or "/"
                return normalize_vfs_path(suffix, cwd="/")

        if user_file.scope == self.persistent_root_scope:
            return self._root_vfs_path_for_user_file(original_path)
        return None

    async def _load_real_files(self) -> list[VFSFile]:
        def _load():
            query = Q(scope=UserFile.Scope.MESSAGE_ATTACHMENT, original_filename__startswith=self.tmp_storage_prefix)
            if self.persistent_root_scope == UserFile.Scope.THREAD_SHARED:
                query |= Q(scope=UserFile.Scope.THREAD_SHARED)
            elif self.persistent_root_prefix:
                query |= Q(
                    scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                    original_filename__startswith=self.persistent_root_prefix,
                )
            if self.legacy_workspace_storage_prefix:
                query |= Q(
                    scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                    original_filename__startswith=self.legacy_workspace_storage_prefix,
                )
            return list(
                UserFile.objects.filter(
                    user=self.user,
                    thread=self.thread,
                ).filter(query)
            )

        user_files = await sync_to_async(_load, thread_sensitive=True)()
        by_path: dict[str, VFSFile] = {}
        for user_file in user_files:
            vfs_path = self._vfs_path_for_user_file(user_file)
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

        entries: dict[str, dict] = {}
        if normalized == "/":
            entries["skills"] = {"name": "skills", "path": "/skills", "type": "dir"}
            entries["tmp"] = {"name": "tmp", "path": "/tmp", "type": "dir"}

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
        item = await self.get_real_file(normalized)
        if item is None or item.user_file is None:
            raise VFSError(f"File not found: {normalized}")
        return await download_file_content(item.user_file), item.mime_type

    async def mkdir(self, path: str) -> str:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized.startswith("/skills"):
            raise VFSError("mkdir is not supported inside /skills.")
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
    ) -> VFSFile:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        scope, storage_path = self._storage_path_for_vfs_path(normalized)
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
        if normalized_source.startswith("/skills/"):
            content = (await self.read_text(normalized_source)).encode("utf-8")
            return await self.write_file(resolved_destination, content, mime_type="text/markdown")
        content, mime_type = await self.read_bytes(normalized_source)
        return await self.write_file(resolved_destination, content, mime_type=mime_type)

    async def move(self, source: str, destination: str) -> str:
        normalized_source = normalize_vfs_path(source, cwd=self.cwd)
        source_name = posixpath.basename(normalized_source) or "file"
        resolved_destination = await self.resolve_output_path(destination, source_name=source_name)
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

        return sorted(set(matches))

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
