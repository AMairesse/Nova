from __future__ import annotations

import posixpath
import shlex

import httpx

from nova.file_utils import MAX_FILE_SIZE
from nova.runtime_v2.capabilities import TerminalCapabilities
from nova.runtime_v2.vfs import VFSError, VirtualFileSystem, normalize_vfs_path
from nova.tools.builtins import browser as browser_builtin
from nova.tools.builtins import code_execution as code_builtin
from nova.tools.builtins import email as email_builtin


class TerminalCommandError(Exception):
    pass


class TerminalExecutor:
    def __init__(self, *, vfs: VirtualFileSystem, capabilities: TerminalCapabilities):
        self.vfs = vfs
        self.capabilities = capabilities

    def _parse(self, command: str) -> list[str]:
        raw = str(command or "").strip()
        if not raw:
            raise TerminalCommandError("Empty command.")
        forbidden_markers = ["|", "&&", "||", ">", "<", "$(", "`"]
        if any(marker in raw for marker in forbidden_markers):
            raise TerminalCommandError(
                "Pipes, redirections, shell substitutions, and command chaining are not supported."
            )
        try:
            return shlex.split(raw)
        except ValueError as exc:
            raise TerminalCommandError(f"Command parse error: {exc}") from exc

    async def execute(self, command: str) -> str:
        self.vfs.remember_command(command)
        tokens = self._parse(command)
        name = tokens[0]

        if name == "pwd":
            return self.vfs.cwd
        if name == "ls":
            return await self._cmd_ls(tokens[1:])
        if name == "cd":
            return await self._cmd_cd(tokens[1:])
        if name == "cat":
            return await self._cmd_cat(tokens[1:])
        if name == "head":
            return await self._cmd_head_tail(tokens[1:], tail=False)
        if name == "tail":
            return await self._cmd_head_tail(tokens[1:], tail=True)
        if name == "mkdir":
            return await self._cmd_mkdir(tokens[1:])
        if name == "cp":
            return await self._cmd_cp(tokens[1:])
        if name == "mv":
            return await self._cmd_mv(tokens[1:])
        if name == "rm":
            return await self._cmd_rm(tokens[1:])
        if name == "find":
            return await self._cmd_find(tokens[1:])
        if name == "wget":
            return await self._cmd_wget(tokens[1:])
        if name == "curl":
            return await self._cmd_curl(tokens[1:])
        if name == "mail":
            return await self._cmd_mail(tokens[1:])
        if name == "python":
            return await self._cmd_python(tokens[1:])

        raise TerminalCommandError(f"Unknown command: {name}")

    async def _cmd_ls(self, args: list[str]) -> str:
        path = args[0] if args else self.vfs.cwd
        normalized = normalize_vfs_path(path, cwd=self.vfs.cwd)
        if not await self.vfs.path_exists(normalized):
            raise TerminalCommandError(f"Path not found: {normalized}")
        if not await self.vfs.is_dir(normalized):
            return normalized
        entries = await self.vfs.list_dir(normalized)
        if not entries:
            return ""
        lines = []
        for entry in entries:
            if entry["type"] == "dir":
                lines.append(f"{entry['name']}/")
            else:
                size = entry.get("size")
                mime_type = entry.get("mime_type", "")
                details = f" ({mime_type}, {size} bytes)" if size is not None else ""
                lines.append(f"{entry['name']}{details}")
        return "\n".join(lines)

    async def _cmd_cd(self, args: list[str]) -> str:
        target = args[0] if args else "/workspace"
        normalized = normalize_vfs_path(target, cwd=self.vfs.cwd)
        if not await self.vfs.path_exists(normalized) or not await self.vfs.is_dir(normalized):
            raise TerminalCommandError(f"Directory not found: {normalized}")
        self.vfs.set_cwd(normalized)
        return self.vfs.cwd

    async def _cmd_cat(self, args: list[str]) -> str:
        if len(args) != 1:
            raise TerminalCommandError("Usage: cat <path>")
        try:
            return await self.vfs.read_text(args[0])
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_head_tail(self, args: list[str], *, tail: bool) -> str:
        if not args:
            raise TerminalCommandError("Usage: head [-n N] <path>")
        line_count = 10
        path = None
        index = 0
        while index < len(args):
            token = args[index]
            if token == "-n":
                index += 1
                if index >= len(args):
                    raise TerminalCommandError("Missing value after -n")
                line_count = max(1, int(args[index]))
            else:
                path = token
            index += 1
        if not path:
            raise TerminalCommandError("Path required.")
        content = await self._cmd_cat([path])
        lines = content.splitlines()
        selected = lines[-line_count:] if tail else lines[:line_count]
        return "\n".join(selected)

    async def _cmd_mkdir(self, args: list[str]) -> str:
        if len(args) != 1:
            raise TerminalCommandError("Usage: mkdir <path>")
        try:
            return f"Created directory {await self.vfs.mkdir(args[0])}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_cp(self, args: list[str]) -> str:
        if len(args) != 2:
            raise TerminalCommandError("Usage: cp <source> <destination>")
        try:
            copied = await self.vfs.copy(args[0], args[1])
            return f"Copied to {copied.path}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_mv(self, args: list[str]) -> str:
        if len(args) != 2:
            raise TerminalCommandError("Usage: mv <source> <destination>")
        try:
            destination = await self.vfs.move(args[0], args[1])
            return f"Moved to {destination}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_rm(self, args: list[str]) -> str:
        if len(args) != 1:
            raise TerminalCommandError("Usage: rm <path>")
        try:
            await self.vfs.remove(args[0])
            return f"Removed {normalize_vfs_path(args[0], cwd=self.vfs.cwd)}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_find(self, args: list[str]) -> str:
        start = args[0] if args else self.vfs.cwd
        term = args[1] if len(args) > 1 else ""
        results = await self.vfs.find(start, term)
        return "\n".join(results)

    def _parse_output_path(self, args: list[str], *,
                           default_filename: str | None = None) -> tuple[str | None, list[str]]:
        output_path = None
        remaining = []
        index = 0
        while index < len(args):
            token = args[index]
            if token in {"--output", "-o", "-O"}:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {token}")
                output_path = args[index]
            else:
                remaining.append(token)
            index += 1
        if output_path is None and default_filename:
            output_path = posixpath.join(self.vfs.cwd, default_filename)
        return output_path, remaining

    async def _download_http(self, url: str) -> tuple[bytes, str, str]:
        timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            content = response.content
            if len(content) > MAX_FILE_SIZE:
                raise TerminalCommandError(f"Downloaded file exceeds the {MAX_FILE_SIZE} byte limit.")
            mime_type = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            inferred_name = browser_builtin._infer_download_filename(url, response.headers)
            return content, mime_type or "application/octet-stream", inferred_name

    async def _cmd_wget(self, args: list[str]) -> str:
        if not self.capabilities.has_web:
            raise TerminalCommandError("Web commands are not enabled for this agent.")
        output_path, remaining = self._parse_output_path(args)
        if len(remaining) != 1:
            raise TerminalCommandError("Usage: wget <url> [--output <path>]")
        url = remaining[0]
        content, mime_type, inferred_name = await self._download_http(url)
        destination = output_path or posixpath.join(self.vfs.cwd, inferred_name)
        written = await self.vfs.write_file(destination, content, mime_type=mime_type)
        return f"Downloaded {url} to {written.path}"

    async def _cmd_curl(self, args: list[str]) -> str:
        if not self.capabilities.has_web:
            raise TerminalCommandError("Web commands are not enabled for this agent.")
        output_path, remaining = self._parse_output_path(args)
        if len(remaining) != 1:
            raise TerminalCommandError("Usage: curl <url> [--output <path>]")
        url = remaining[0]
        content, mime_type, inferred_name = await self._download_http(url)
        if output_path:
            written = await self.vfs.write_file(output_path, content, mime_type=mime_type)
            return f"Downloaded {url} to {written.path}"
        if mime_type.startswith("text/") or mime_type in {"application/json", "application/xml"}:
            try:
                return content.decode("utf-8")[:8000]
            except UnicodeDecodeError:
                pass
        return (
            f"Binary response from {url} ({mime_type}, {len(content)} bytes). "
            f"Use curl --output {posixpath.join(self.vfs.cwd, inferred_name)} to save it."
        )

    def _parse_flag_value(self, args: list[str], flag: str) -> tuple[str | None, list[str]]:
        remaining: list[str] = []
        value = None
        index = 0
        while index < len(args):
            token = args[index]
            if token == flag:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {flag}")
                value = args[index]
            else:
                remaining.append(token)
            index += 1
        return value, remaining

    def _parse_multi_flag(self, args: list[str], flag: str) -> tuple[list[str], list[str]]:
        values: list[str] = []
        remaining: list[str] = []
        index = 0
        while index < len(args):
            token = args[index]
            if token == flag:
                index += 1
                if index >= len(args):
                    raise TerminalCommandError(f"Missing value after {flag}")
                values.append(args[index])
            else:
                remaining.append(token)
            index += 1
        return values, remaining

    async def _cmd_mail(self, args: list[str]) -> str:
        if not self.capabilities.has_email:
            raise TerminalCommandError("Mail commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError("Usage: mail <list|read|attachments|import|send> ...")
        tool = self.capabilities.email_tool
        subcommand = args[0]
        remainder = args[1:]

        if subcommand == "list":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            limit, remainder = self._parse_flag_value(remainder, "--limit")
            if remainder:
                raise TerminalCommandError("Unexpected arguments for mail list.")
            return await email_builtin.list_emails(
                self.vfs.user,
                tool.id,
                folder=folder or "INBOX",
                limit=int(limit or 10),
            )

        if subcommand == "read":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            full = "--full" in remainder
            remainder = [item for item in remainder if item != "--full"]
            if len(remainder) != 1:
                raise TerminalCommandError("Usage: mail read <id> [--folder F] [--full]")
            return await email_builtin.read_email(
                self.vfs.user,
                tool.id,
                int(remainder[0]),
                folder=folder or "INBOX",
                preview_only=not full,
            )

        if subcommand == "attachments":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            if len(remainder) != 1:
                raise TerminalCommandError("Usage: mail attachments <id> [--folder F]")
            return await email_builtin.list_email_attachments(
                self.vfs.user,
                tool.id,
                int(remainder[0]),
                folder=folder or "INBOX",
            )

        if subcommand == "import":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            attachment_id, remainder = self._parse_flag_value(remainder, "--attachment")
            output_path, remainder = self._parse_flag_value(remainder, "--output")
            if len(remainder) != 1 or not attachment_id:
                raise TerminalCommandError(
                    "Usage: mail import <id> --attachment <part> [--folder F] [--output PATH]"
                )
            message_id = int(remainder[0])
            _envelope, _message, _uid, attachments = await email_builtin._load_email_message_with_attachments(
                self.vfs.user,
                tool.id,
                message_id,
                folder=folder or "INBOX",
            )
            selected = next(
                (item for item in attachments if str(item.get("attachment_id")) == str(attachment_id)),
                None,
            )
            if selected is None:
                raise TerminalCommandError(f"Attachment {attachment_id} not found on email {message_id}.")
            destination = output_path or posixpath.join(
                self.vfs.cwd,
                str(selected.get("filename") or f"attachment-{attachment_id}"),
            )
            written = await self.vfs.write_file(
                destination,
                bytes(selected.get("content") or b""),
                mime_type=str(selected.get("mime_type") or "application/octet-stream"),
            )
            return f"Imported attachment to {written.path}"

        if subcommand == "send":
            to, remainder = self._parse_flag_value(remainder, "--to")
            cc, remainder = self._parse_flag_value(remainder, "--cc")
            subject, remainder = self._parse_flag_value(remainder, "--subject")
            body_file, remainder = self._parse_flag_value(remainder, "--body-file")
            attach_paths, remainder = self._parse_multi_flag(remainder, "--attach")
            if remainder or not to or not subject or not body_file:
                raise TerminalCommandError(
                    "Usage: mail send --to <addr> --subject <subject> --body-file <path> [--cc <addr>] [--attach <path> ...]"
                )
            body = await self.vfs.read_text(body_file)
            return await self._send_mail_direct(
                tool=tool,
                to=to,
                cc=cc,
                subject=subject,
                body=body,
                attach_paths=attach_paths,
            )

        raise TerminalCommandError(f"Unknown mail subcommand: {subcommand}")

    async def _send_mail_direct(self, *, tool, to: str, cc: str | None,
                                subject: str, body: str, attach_paths: list[str]) -> str:
        credential = await email_builtin._get_credential(self.vfs.user, tool.id)
        if credential is None:
            raise TerminalCommandError("No email credential found.")

        smtp_server = credential.config.get("smtp_server")
        username = credential.config.get("username")
        from_address = credential.config.get("from_address", username)
        if not smtp_server:
            raise TerminalCommandError("SMTP server not configured.")

        msg = email_builtin.MIMEMultipart()
        msg["From"] = from_address
        msg["To"] = to
        msg["Subject"] = subject
        if cc:
            msg["Cc"] = cc
        msg.attach(email_builtin.MIMEText(body, "plain"))

        attachments = []
        for attach_path in attach_paths:
            content, mime_type = await self.vfs.read_bytes(attach_path)
            attachments.append(
                type(
                    "ResolvedAttachment",
                    (),
                    {
                        "filename": posixpath.basename(normalize_vfs_path(attach_path, cwd=self.vfs.cwd)),
                        "mime_type": mime_type,
                        "content": content,
                    },
                )()
            )
        email_builtin._attach_binary_parts(msg, attachments)

        server = None
        try:
            server = email_builtin.build_smtp_client(credential)
            recipients = [to]
            if cc:
                recipients.extend([item.strip() for item in cc.split(",") if str(item or "").strip()])
            server.sendmail(from_address, recipients, msg.as_string())
        finally:
            email_builtin.safe_smtp_quit(server)

        return f"Email sent successfully to {to}"

    async def _cmd_python(self, args: list[str]) -> str:
        if not self.capabilities.has_python:
            raise TerminalCommandError("Python execution is not enabled for this agent.")
        tool = self.capabilities.code_execution_tool
        config = await code_builtin.get_judge0_config(tool)
        host = config["url"]
        timeout = int(config.get("timeout") or 5)

        if not args:
            raise TerminalCommandError("Usage: python <script.py> or python -c \"...\"")
        if args[0] == "-c":
            if len(args) < 2:
                raise TerminalCommandError("Usage: python -c \"...\"")
            code = args[1]
            return await code_builtin.execute_code(host, code, language="python", timeout=timeout)

        if len(args) != 1:
            raise TerminalCommandError("Usage: python <script.py>")
        script_path = args[0]
        code = await self.vfs.read_text(script_path)
        return await code_builtin.execute_code(host, code, language="python", timeout=timeout)
