from __future__ import annotations

import os
import posixpath
import shlex
import textwrap
from dataclasses import dataclass
from pathlib import Path


SKILLS_ROOT = "/skills"
INBOX_ROOT = "/inbox"
HISTORY_ROOT = "/history"
MEMORY_ROOT = "/memory"
WEBDAV_ROOT = "/webdav"

RUNNER_INTERNAL_DIRNAME = ".nova_runner"
RUNNER_COMMAND_FILENAME = "command.sh"
RUNNER_ENV_FILENAME = "env.json"
RUNNER_CWD_FILENAME = "cwd.txt"

READ_ONLY_PROJECTION_ROOTS = (SKILLS_ROOT, INBOX_ROOT, HISTORY_ROOT)
SHELL_SPECIAL_PATH_PREFIXES = (
    "/dev/",
    "/proc/",
    "/sys/",
)
EXCLUDED_SYNC_ROOTS = {
    "/",
    SKILLS_ROOT,
    INBOX_ROOT,
    HISTORY_ROOT,
    MEMORY_ROOT,
    WEBDAV_ROOT,
}
EXCLUDED_SYNC_PREFIXES = tuple(
    f"{root.rstrip('/')}/" for root in EXCLUDED_SYNC_ROOTS if root != "/"
)

PYTHON_WORKSPACE_SITECUSTOMIZE_SOURCE = textwrap.dedent(
    """\
    import builtins
    import io
    import os


    def _nova_install_python_workspace_shims(workspace_root):
        workspace_root = str(workspace_root or "").strip()
        if not workspace_root:
            return lambda: None

        preserved_prefixes = ("/dev", "/proc", "/sys")
        originals = {}
        def _translate(path):
            if isinstance(path, int) or path is None:
                return path
            try:
                rendered = os.fspath(path)
            except TypeError:
                return path
            if isinstance(rendered, bytes):
                return path
            if not isinstance(rendered, str) or not rendered.startswith("/"):
                return path
            if rendered == "/":
                return workspace_root
            if rendered in preserved_prefixes:
                return rendered
            if any(rendered.startswith(prefix + "/") for prefix in preserved_prefixes):
                return rendered
            return os.path.join(workspace_root, rendered.lstrip("/"))

        def _wrap_single(module, name):
            original = getattr(module, name)
            originals[(module, name)] = original

            def wrapper(*args, **kwargs):
                if not args:
                    return original(*args, **kwargs)
                mapped_args = (_translate(args[0]),) + args[1:]
                return original(*mapped_args, **kwargs)

            setattr(module, name, wrapper)

        def _wrap_double(module, name):
            original = getattr(module, name)
            originals[(module, name)] = original

            def wrapper(*args, **kwargs):
                if len(args) < 2:
                    return original(*args, **kwargs)
                mapped_args = (_translate(args[0]), _translate(args[1])) + args[2:]
                return original(*mapped_args, **kwargs)

            setattr(module, name, wrapper)

        for module, name in (
            (builtins, "open"),
            (io, "open"),
            (os, "access"),
            (os, "chdir"),
            (os, "listdir"),
            (os, "mkdir"),
            (os, "makedirs"),
            (os, "open"),
            (os, "remove"),
            (os, "rmdir"),
            (os, "scandir"),
            (os, "stat"),
            (os, "lstat"),
            (os, "unlink"),
        ):
            _wrap_single(module, name)

        for module, name in ((os, "rename"), (os, "replace")):
            _wrap_double(module, name)

        def _restore():
            for (module, name), original in originals.items():
                setattr(module, name, original)

        return _restore


    _restore_nova_workspace_shims = _nova_install_python_workspace_shims(
        os.environ.get("NOVA_WORKSPACE_ROOT", "")
    )
    """
)


class ExecRunnerError(Exception):
    pass


@dataclass(slots=True, frozen=True)
class ExecSessionSelector:
    user_id: int | str
    thread_id: int | str
    agent_id: int | str

    @property
    def session_id(self) -> str:
        return (
            f"user-{self._slug(self.user_id)}"
            f"--thread-{self._slug(self.thread_id)}"
            f"--agent-{self._slug(self.agent_id)}"
        )

    @staticmethod
    def _slug(value: int | str) -> str:
        text = str(value or "").strip() or "unknown"
        return "".join(
            character if character.isalnum() or character in {"-", "_"} else "-"
            for character in text
        ).strip("-") or "unknown"


@dataclass(slots=True, frozen=True)
class SandboxShellResult:
    stdout: str
    stderr: str
    status: int
    cwd_after: str
    execution_plane: str = "sandbox"


def normalize_sandbox_path(raw_path: str, *, cwd: str = "/") -> str:
    candidate = str(raw_path or "").strip()
    if not candidate:
        candidate = cwd or "/"
    if not candidate.startswith("/"):
        candidate = posixpath.join(cwd or "/", candidate)
    normalized = posixpath.normpath(candidate)
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized


def workspace_path_for_vfs_path(workspace_root: Path, vfs_path: str) -> Path:
    normalized = normalize_sandbox_path(vfs_path, cwd="/")
    if normalized == "/":
        return workspace_root
    return workspace_root / normalized.lstrip("/")


def vfs_path_for_workspace_path(workspace_root: Path, raw_path: str) -> str:
    text = str(raw_path or "").strip()
    if not text:
        return "/"
    try:
        relative = Path(text).resolve().relative_to(workspace_root.resolve())
    except Exception:
        return normalize_sandbox_path(text, cwd="/")
    if not relative.parts:
        return "/"
    return normalize_sandbox_path("/" + relative.as_posix(), cwd="/")


def iter_normalized_workspace_files(workspace_root: Path) -> tuple[set[str], set[str]]:
    directories: set[str] = {"/tmp"}
    files: set[str] = set()
    for current_root, dirnames, filenames in os.walk(workspace_root):
        current = Path(current_root)
        relative_root = current.relative_to(workspace_root)
        if relative_root.parts and relative_root.parts[0] == RUNNER_INTERNAL_DIRNAME:
            dirnames[:] = []
            continue
        normalized_dir = "/" if not relative_root.parts else "/" + "/".join(relative_root.parts)
        if normalized_dir != "/":
            directories.add(normalize_sandbox_path(normalized_dir, cwd="/"))
        for dirname in list(dirnames):
            if dirname == RUNNER_INTERNAL_DIRNAME:
                dirnames.remove(dirname)
        for filename in filenames:
            relative_path = relative_root / filename if relative_root.parts else Path(filename)
            normalized = normalize_sandbox_path("/" + relative_path.as_posix(), cwd="/")
            if normalized.startswith(f"/{RUNNER_INTERNAL_DIRNAME}/"):
                continue
            files.add(normalized)
    return directories, files


def encode_environment_script(env: dict[str, str]) -> str:
    lines = ["set +e"]
    for key in sorted(env.keys()):
        value = str(env[key])
        safe_value = value.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'export {key}="{safe_value}"')
    return "\n".join(lines)


def rewrite_token_for_workspace(token: str, workspace_root: Path) -> str:
    value = str(token or "")
    if not value or value in {"|", ";", "&&", "||", ">", ">>", "<", "<<", ">&", "<&", "&>", "&>>"}:
        return value
    if "://" in value:
        return value
    if value == "/":
        return str(workspace_root)
    if value in {"/dev", "/proc", "/sys"} or value.startswith(SHELL_SPECIAL_PATH_PREFIXES):
        return value
    if not value.startswith("/"):
        return value
    return str(workspace_path_for_vfs_path(workspace_root, value))


def rewrite_shell_command_for_workspace(command: str, workspace_root: Path) -> str:
    raw = str(command or "")
    try:
        lexer = shlex.shlex(raw, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return raw
    rewritten = [rewrite_token_for_workspace(token, workspace_root) for token in tokens]
    rendered_tokens: list[str] = []
    operator_tokens = {"|", ";", "&&", "||", ">", ">>", "<", "<<", ">&", "<&", "&>", "&>>"}
    fd_redirect_operators = {">", ">>", "<", "<<", "<&", ">&"}
    index = 0
    while index < len(rewritten):
        token = rewritten[index]
        if token.isdigit() and index + 1 < len(rewritten) and rewritten[index + 1] in fd_redirect_operators:
            rendered_tokens.append(f"{token}{rewritten[index + 1]}")
            index += 2
            continue
        if token in operator_tokens:
            rendered_tokens.append(token)
        else:
            has_shell_substitution = "$(" in token or "`" in token
            needs_quotes = (
                not token
                or any(character.isspace() for character in token)
                or (
                    any(character in token for character in "\"'<>;&|()")
                    and not has_shell_substitution
                )
            )
            rendered_tokens.append(shlex.quote(token) if needs_quotes else token)
        index += 1
    return " ".join(rendered_tokens)


def rewrite_output_paths_from_workspace(text: str, workspace_root: Path) -> str:
    rendered = str(text or "")
    root = str(workspace_root)
    if not root:
        return rendered
    candidates = {
        f"{root}/tmp": "/tmp",
        f"{root}/skills": SKILLS_ROOT,
        f"{root}/inbox": INBOX_ROOT,
        f"{root}/history": HISTORY_ROOT,
        f"{root}/": "/",
        root: "/",
    }
    for candidate, replacement in sorted(
        candidates.items(),
        key=lambda item: len(item[0]),
        reverse=True,
    ):
        rendered = rendered.replace(candidate, replacement)
    return rendered
