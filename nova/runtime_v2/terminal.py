from __future__ import annotations

import posixpath
import shlex
from datetime import timezone as dt_timezone
from types import SimpleNamespace

import httpx
from django.utils import timezone

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
        self._mailbox_registry_cache = None

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
        if name == "touch":
            return await self._cmd_touch(tokens[1:])
        if name == "tee":
            return await self._cmd_tee(tokens[1:])
        if name == "cp":
            return await self._cmd_cp(tokens[1:])
        if name == "mv":
            return await self._cmd_mv(tokens[1:])
        if name == "rm":
            return await self._cmd_rm(tokens[1:])
        if name == "find":
            return await self._cmd_find(tokens[1:])
        if name == "date":
            return await self._cmd_date(tokens[1:])
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
        target = args[0] if args else "/"
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

    def _validate_text_write_path(self, raw_path: str) -> str:
        normalized = normalize_vfs_path(raw_path, cwd=self.vfs.cwd)
        if normalized.startswith("/skills"):
            raise TerminalCommandError("Writing into /skills is not supported.")
        return normalized

    async def _cmd_touch(self, args: list[str]) -> str:
        if len(args) != 1:
            raise TerminalCommandError("Usage: touch <path>")
        normalized = self._validate_text_write_path(args[0])
        if await self.vfs.is_dir(normalized):
            raise TerminalCommandError(f"Cannot touch a directory: {normalized}")
        existing = await self.vfs.get_real_file(normalized)
        if existing is not None:
            return f"Touched {normalized}"
        try:
            written = await self.vfs.write_file(normalized, b"", mime_type="text/plain")
            return f"Created empty file {written.path}"
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc

    async def _cmd_tee(self, args: list[str]) -> str:
        if not args:
            raise TerminalCommandError('Usage: tee <path> --text "<content>" [--append]')
        append = "--append" in args
        remainder = [item for item in args if item != "--append"]
        text, remainder = self._parse_flag_value(remainder, "--text")
        if len(remainder) != 1 or text is None:
            raise TerminalCommandError('Usage: tee <path> --text "<content>" [--append]')

        normalized = self._validate_text_write_path(remainder[0])
        if await self.vfs.is_dir(normalized):
            raise TerminalCommandError(f"Cannot write text into a directory: {normalized}")

        content = str(text)
        if append and await self.vfs.path_exists(normalized):
            try:
                existing_text = await self.vfs.read_text(normalized)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            content = f"{existing_text}{content}"

        encoded = content.encode("utf-8")
        try:
            written = await self.vfs.write_file(
                normalized,
                encoded,
                mime_type="text/plain",
                overwrite=True,
            )
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
        return f"Wrote {len(str(text).encode('utf-8'))} bytes to {written.path}"

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

    async def _cmd_date(self, args: list[str]) -> str:
        if not self.capabilities.has_date_time:
            raise TerminalCommandError("Date/time commands are not enabled for this agent.")

        use_utc = False
        format_token = None
        for token in args:
            if token == "-u":
                use_utc = True
                continue
            if token in {"+%F", "+%T"} and format_token is None:
                format_token = token
                continue
            raise TerminalCommandError("Usage: date [-u] [+%F|+%T]")

        now = timezone.now()
        current = now.astimezone(dt_timezone.utc) if use_utc else timezone.localtime(now)
        if format_token == "+%F":
            return current.strftime("%Y-%m-%d")
        if format_token == "+%T":
            return current.strftime("%H:%M:%S")
        zone_label = "UTC" if use_utc else str(current.tzname() or timezone.get_current_timezone_name())
        return f"{current.strftime('%Y-%m-%d %H:%M:%S')} {zone_label}"

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
        try:
            destination = await self.vfs.resolve_output_path(destination, source_name=inferred_name)
        except VFSError as exc:
            raise TerminalCommandError(str(exc)) from exc
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
            try:
                resolved_output = await self.vfs.resolve_output_path(output_path, source_name=inferred_name)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            written = await self.vfs.write_file(resolved_output, content, mime_type=mime_type)
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

    async def _get_mailbox_registry(self):
        if self._mailbox_registry_cache is None:
            agent = SimpleNamespace(user=self.vfs.user, thread=self.vfs.thread)
            self._mailbox_registry_cache = await email_builtin._build_mailbox_registry(
                list(self.capabilities.email_tools or []),
                agent,
            )
        return self._mailbox_registry_cache

    async def _resolve_terminal_mailbox(self, mailbox: str | None):
        _user, entries, lookup, _mailbox_schema, selector_values = await self._get_mailbox_registry()
        requested = str(mailbox or "").strip()
        if not requested:
            if len(entries) == 1:
                return entries[0], selector_values
            raise TerminalCommandError(
                "The --mailbox selector is required when multiple mailboxes are configured. "
                f"Available addresses: {', '.join(selector_values)}."
            )

        entry, err = email_builtin._resolve_mailbox(requested, lookup, selector_values)
        if err:
            raise TerminalCommandError(err)
        return entry, selector_values

    async def _cmd_mail_accounts(self) -> str:
        _user, entries, _lookup, _mailbox_schema, selector_values = await self._get_mailbox_registry()
        if not entries:
            raise TerminalCommandError("No email mailbox is configured for this agent.")
        lines = ["Configured mailboxes:"]
        for entry in entries:
            label = str(entry.get("display_label") or "").strip()
            label_part = f", label: {label}" if label else ""
            sending = "enabled" if entry.get("can_send") else "disabled"
            lines.append(
                f"- {entry['selector_email']} (sending: {sending}{label_part})"
            )
        if len(selector_values) > 1:
            lines.append("Pass --mailbox <email> on mail commands to choose an account explicitly.")
        return "\n".join(lines)

    async def _cmd_mail(self, args: list[str]) -> str:
        if not self.capabilities.has_email:
            raise TerminalCommandError("Mail commands are not enabled for this agent.")
        if not args:
            raise TerminalCommandError("Usage: mail <accounts|list|read|attachments|import|send|folders> ...")
        subcommand = args[0]
        remainder = args[1:]
        mailbox, remainder = self._parse_flag_value(remainder, "--mailbox")

        if subcommand == "accounts":
            if remainder:
                raise TerminalCommandError("Usage: mail accounts")
            return await self._cmd_mail_accounts()

        entry, _selector_values = await self._resolve_terminal_mailbox(mailbox)
        tool_id = int(entry["tool_id"])

        if subcommand == "list":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            limit, remainder = self._parse_flag_value(remainder, "--limit")
            if remainder:
                raise TerminalCommandError("Usage: mail list [--mailbox <email>] [--folder INBOX] [--limit N]")
            return await email_builtin.list_emails(
                self.vfs.user,
                tool_id,
                folder=folder or "INBOX",
                limit=int(limit or 10),
            )

        if subcommand == "read":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            full = "--full" in remainder
            remainder = [item for item in remainder if item != "--full"]
            if len(remainder) != 1:
                raise TerminalCommandError(
                    "Usage: mail read [--mailbox <email>] <id> [--folder F] [--full]"
                )
            return await email_builtin.read_email(
                self.vfs.user,
                tool_id,
                int(remainder[0]),
                folder=folder or "INBOX",
                preview_only=not full,
            )

        if subcommand == "attachments":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            if len(remainder) != 1:
                raise TerminalCommandError(
                    "Usage: mail attachments [--mailbox <email>] <id> [--folder F]"
                )
            return await email_builtin.list_email_attachments(
                self.vfs.user,
                tool_id,
                int(remainder[0]),
                folder=folder or "INBOX",
            )

        if subcommand == "folders":
            if remainder:
                raise TerminalCommandError("Usage: mail folders [--mailbox <email>]")
            return await email_builtin.list_mailboxes(self.vfs.user, tool_id)

        if subcommand == "import":
            folder, remainder = self._parse_flag_value(remainder, "--folder")
            attachment_id, remainder = self._parse_flag_value(remainder, "--attachment")
            output_path, remainder = self._parse_flag_value(remainder, "--output")
            if len(remainder) != 1 or not attachment_id:
                raise TerminalCommandError(
                    "Usage: mail import [--mailbox <email>] <id> --attachment <part> [--folder F] [--output PATH]"
                )
            message_id = int(remainder[0])
            _envelope, _message, _uid, attachments = await email_builtin._load_email_message_with_attachments(
                self.vfs.user,
                tool_id,
                message_id,
                folder=folder or "INBOX",
            )
            selected = next(
                (item for item in attachments if str(item.get("attachment_id")) == str(attachment_id)),
                None,
            )
            if selected is None:
                raise TerminalCommandError(f"Attachment {attachment_id} not found on email {message_id}.")
            source_name = str(selected.get("filename") or f"attachment-{attachment_id}")
            destination = output_path or posixpath.join(self.vfs.cwd, source_name)
            try:
                destination = await self.vfs.resolve_output_path(destination, source_name=source_name)
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
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
                    "Usage: mail send [--mailbox <email>] --to <addr> --subject <subject> "
                    "--body-file <path> [--cc <addr>] [--attach <path> ...]"
                )
            if not entry.get("can_send"):
                raise TerminalCommandError(
                    f"Sending is disabled for mailbox '{entry['selector_email']}'."
                )
            body = await self.vfs.read_text(body_file)
            return await self._send_mail_direct(
                tool_id=tool_id,
                to=to,
                cc=cc,
                subject=subject,
                body=body,
                attach_paths=attach_paths,
            )

        raise TerminalCommandError(f"Unknown mail subcommand: {subcommand}")

    async def _send_mail_direct(self, *, tool_id: int, to: str, cc: str | None,
                                subject: str, body: str, attach_paths: list[str]) -> str:
        credential = await email_builtin._get_credential(self.vfs.user, tool_id)
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

    @staticmethod
    def _extract_python_stdout(result: str) -> str:
        marker_stdout = "\nStdout: "
        marker_stderr = "\nStderr: "
        stdout_index = result.find(marker_stdout)
        if stdout_index == -1:
            return ""
        start = stdout_index + len(marker_stdout)
        stderr_index = result.find(marker_stderr, start)
        if stderr_index == -1:
            return result[start:]
        return result[start:stderr_index]

    async def _cmd_python(self, args: list[str]) -> str:
        if not self.capabilities.has_python:
            raise TerminalCommandError("Python execution is not enabled for this agent.")
        tool = self.capabilities.code_execution_tool
        config = await code_builtin.get_judge0_config(tool)
        host = config["url"]
        timeout = int(config.get("timeout") or 5)

        if not args:
            raise TerminalCommandError("Usage: python [--output PATH] <script.py> or python [--output PATH] -c \"...\"")

        output_path, remaining = self._parse_output_path(args)
        if remaining and remaining[0] == "-c":
            if len(remaining) != 2:
                raise TerminalCommandError("Usage: python [--output PATH] -c \"...\"")
            code = remaining[1]
            result = await code_builtin.execute_code(host, code, language="python", timeout=timeout)
        else:
            if len(remaining) != 1:
                raise TerminalCommandError("Usage: python [--output PATH] <script.py>")
            script_path = remaining[0]
            code = await self.vfs.read_text(script_path)
            result = await code_builtin.execute_code(host, code, language="python", timeout=timeout)

        if output_path:
            output_name = "python-stdout.txt"
            if remaining and remaining[0] != "-c":
                script_name = posixpath.basename(normalize_vfs_path(remaining[0], cwd=self.vfs.cwd)) or "python"
                stem, _ext = posixpath.splitext(script_name)
                output_name = f"{stem or 'python'}.stdout.txt"
            try:
                resolved_output = await self.vfs.resolve_output_path(
                    self._validate_text_write_path(output_path),
                    source_name=output_name,
                )
            except VFSError as exc:
                raise TerminalCommandError(str(exc)) from exc
            stdout = self._extract_python_stdout(result)
            await self.vfs.write_file(
                resolved_output,
                stdout.encode("utf-8"),
                mime_type="text/plain",
                overwrite=True,
            )

        return result
