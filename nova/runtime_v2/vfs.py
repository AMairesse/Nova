from __future__ import annotations

import posixpath
from dataclasses import dataclass

from asgiref.sync import sync_to_async
from django.db.models import Q

from nova.file_utils import batch_upload_files, download_file_content
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


def normalize_vfs_path(raw_path: str, *, cwd: str = "/workspace") -> str:
    candidate = str(raw_path or "").strip()
    if not candidate:
        candidate = cwd or "/workspace"
    if not candidate.startswith("/"):
        candidate = posixpath.join(cwd or "/workspace", candidate)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


class VirtualFileSystem:
    def __init__(self, *, thread, user, agent_config, session_state: dict, skill_registry: dict[str, str]):
        self.thread = thread
        self.user = user
        self.agent_config = agent_config
        self.session_state = session_state
        self.skill_registry = dict(skill_registry or {})

    @property
    def _workspace_storage_prefix(self) -> str:
        return f"{RUNTIME_STORAGE_ROOT}/{int(self.agent_config.id)}/workspace"

    @property
    def _tmp_storage_prefix(self) -> str:
        return f"{RUNTIME_STORAGE_ROOT}/{int(self.agent_config.id)}/tmp"

    @property
    def cwd(self) -> str:
        return normalize_vfs_path(self.session_state.get("cwd", "/workspace"), cwd="/workspace")

    def set_cwd(self, path: str) -> None:
        self.session_state["cwd"] = normalize_vfs_path(path, cwd=self.cwd)

    def remember_command(self, command: str) -> None:
        history = [str(item) for item in list(self.session_state.get("history") or []) if str(item).strip()]
        history.append(str(command))
        self.session_state["history"] = history[-50:]

    def _get_session_dirs(self) -> set[str]:
        return {
            normalize_vfs_path(path, cwd="/workspace")
            for path in list(self.session_state.get("directories") or [])
        }

    def _set_session_dirs(self, dirs: set[str]) -> None:
        self.session_state["directories"] = sorted(dirs)

    def _storage_path_for_vfs_path(self, path: str) -> tuple[str, str]:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized == "/thread" or normalized.startswith("/thread/"):
            suffix = normalized[len("/thread"):] or "/"
            return UserFile.Scope.THREAD_SHARED, suffix
        if normalized == "/workspace" or normalized.startswith("/workspace/"):
            suffix = normalized[len("/workspace"):] or "/"
            return UserFile.Scope.MESSAGE_ATTACHMENT, f"{self._workspace_storage_prefix}{suffix}"
        if normalized == "/tmp" or normalized.startswith("/tmp/"):
            suffix = normalized[len("/tmp"):] or "/"
            return UserFile.Scope.MESSAGE_ATTACHMENT, f"{self._tmp_storage_prefix}{suffix}"
        raise VFSError(f"Unsupported writable path: {normalized}")

    def _vfs_path_for_user_file(self, user_file: UserFile) -> str | None:
        original_path = str(user_file.original_filename or "").strip()
        if user_file.scope == UserFile.Scope.THREAD_SHARED:
            return f"/thread{original_path or ''}"
        if user_file.scope == UserFile.Scope.MESSAGE_ATTACHMENT:
            if original_path.startswith(self._workspace_storage_prefix):
                suffix = original_path[len(self._workspace_storage_prefix):] or ""
                return f"/workspace{suffix}"
            if original_path.startswith(self._tmp_storage_prefix):
                suffix = original_path[len(self._tmp_storage_prefix):] or ""
                return f"/tmp{suffix}"
        return None

    async def _load_real_files(self) -> list[VFSFile]:
        def _load():
            return list(
                UserFile.objects.filter(
                    user=self.user,
                    thread=self.thread,
                ).filter(
                    Q(scope=UserFile.Scope.THREAD_SHARED)
                    | Q(
                        scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                        original_filename__startswith=self._workspace_storage_prefix,
                    )
                    | Q(
                        scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                        original_filename__startswith=self._tmp_storage_prefix,
                    )
                )
            )

        user_files = await sync_to_async(_load, thread_sensitive=True)()
        result: list[VFSFile] = []
        for user_file in user_files:
            vfs_path = self._vfs_path_for_user_file(user_file)
            if not vfs_path:
                continue
            result.append(
                VFSFile(
                    path=normalize_vfs_path(vfs_path, cwd="/"),
                    user_file=user_file,
                    mime_type=str(user_file.mime_type or "application/octet-stream"),
                    size=int(user_file.size or 0),
                )
            )
        return result

    async def path_exists(self, path: str) -> bool:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized in {"/", "/skills", "/thread", "/workspace", "/tmp"}:
            return True
        if normalized == "/skills" or normalized.startswith("/skills/"):
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
        if normalized in {"/", "/skills", "/thread", "/workspace", "/tmp"}:
            return True
        if normalized in self._get_session_dirs():
            return True
        for item in await self._load_real_files():
            if item.path.startswith(f"{normalized.rstrip('/')}/"):
                return True
        return False

    async def list_dir(self, path: str | None = None) -> list[dict]:
        normalized = normalize_vfs_path(path or self.cwd, cwd=self.cwd)
        if normalized == "/":
            return [
                {"name": "skills", "path": "/skills", "type": "dir"},
                {"name": "thread", "path": "/thread", "type": "dir"},
                {"name": "workspace", "path": "/workspace", "type": "dir"},
                {"name": "tmp", "path": "/tmp", "type": "dir"},
            ]

        if normalized == "/skills":
            return [
                {"name": name, "path": f"/skills/{name}", "type": "file"}
                for name in sorted(self.skill_registry.keys())
            ]

        entries: dict[str, dict] = {}
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
        except UnicodeDecodeError:
            raise VFSError(
                f"Binary file cannot be displayed as text: {normalized} ({item.mime_type}, {item.size} bytes)"
            )

    async def read_bytes(self, path: str) -> tuple[bytes, str]:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        item = await self.get_real_file(normalized)
        if item is None or item.user_file is None:
            raise VFSError(f"File not found: {normalized}")
        return await download_file_content(item.user_file), item.mime_type

    async def mkdir(self, path: str) -> str:
        normalized = normalize_vfs_path(path, cwd=self.cwd)
        if normalized.startswith("/thread"):
            raise VFSError("mkdir is only supported in /workspace and /tmp.")
        if not (normalized.startswith("/workspace") or normalized.startswith("/tmp")):
            raise VFSError(f"Unsupported directory path: {normalized}")
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

    async def copy(self, source: str, destination: str) -> VFSFile:
        normalized_source = normalize_vfs_path(source, cwd=self.cwd)
        if normalized_source.startswith("/skills/"):
            content = (await self.read_text(normalized_source)).encode("utf-8")
            return await self.write_file(destination, content, mime_type="text/markdown")
        content, mime_type = await self.read_bytes(normalized_source)
        return await self.write_file(destination, content, mime_type=mime_type)

    async def move(self, source: str, destination: str) -> str:
        normalized_source = normalize_vfs_path(source, cwd=self.cwd)
        normalized_destination = normalize_vfs_path(destination, cwd=self.cwd)
        item = await self.get_real_file(normalized_source)
        if item is None or item.user_file is None:
            raise VFSError(f"File not found: {normalized_source}")

        src_scope, _src_storage = self._storage_path_for_vfs_path(normalized_source)
        dst_scope, dst_storage = self._storage_path_for_vfs_path(normalized_destination)
        if src_scope != dst_scope:
            await self.copy(normalized_source, normalized_destination)
            await self.remove(normalized_source)
            return normalized_destination

        def _save():
            item.user_file.original_filename = dst_storage
            item.user_file.scope = dst_scope
            item.user_file.save(update_fields=["original_filename", "scope", "updated_at"])

        await sync_to_async(_save, thread_sensitive=True)()
        return normalized_destination

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
