from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Any

from types import SimpleNamespace

from nova.api_tools import service as api_tools_service
from nova.caldav import service as caldav_service
from nova.mcp import service as mcp_service
from nova.plugins.mail import service as mail_service
from nova.runtime.terminal_metrics import FAILURE_KIND_INVALID_ARGUMENTS
from nova.runtime.vfs import VFSError

if TYPE_CHECKING:
    from nova.runtime.terminal import TerminalExecutor


def _terminal_command_error(*args, **kwargs):
    from nova.runtime.terminal import TerminalCommandError

    return TerminalCommandError(*args, **kwargs)


async def cmd_mail_accounts(executor: TerminalExecutor) -> str:
    _user, entries, _lookup, _mailbox_schema, selector_values = await executor._get_mailbox_registry()
    if not entries:
        raise _terminal_command_error("No email mailbox is configured for this agent.")
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


async def cmd_mail(executor: TerminalExecutor, args: list[str]) -> str:
    if not executor.capabilities.has_email:
        raise _terminal_command_error("Mail commands are not enabled for this agent.")
    if not args:
        raise _terminal_command_error("Usage: mail <accounts|list|read|attachments|import|folders|move|mark|send> ...")
    subcommand = args[0]
    remainder = args[1:]
    mailbox, remainder = executor._parse_flag_value(remainder, "--mailbox")

    if subcommand == "accounts":
        if remainder:
            raise _terminal_command_error("Usage: mail accounts")
        return await cmd_mail_accounts(executor)

    entry, _selector_values = await executor._resolve_terminal_mailbox(mailbox)
    tool_id = int(entry["tool_id"])

    if subcommand == "list":
        folder, remainder = executor._parse_flag_value(remainder, "--folder")
        limit, remainder = executor._parse_flag_value(remainder, "--limit")
        if remainder:
            raise _terminal_command_error("Usage: mail list [--mailbox <email>] [--folder INBOX] [--limit N]")
        return await mail_service.list_emails(
            executor.vfs.user,
            tool_id,
            folder=folder or "INBOX",
            limit=int(limit or 10),
        )

    if subcommand == "read":
        folder, remainder = executor._parse_flag_value(remainder, "--folder")
        full = "--full" in remainder
        remainder = [item for item in remainder if item != "--full"]
        message_id, uid = executor._parse_mail_single_selector(
            remainder,
            usage="Usage: mail read [--mailbox <email>] [--folder F] (<id> | --uid <uid>) [--full]",
        )
        return await mail_service.read_email(
            executor.vfs.user,
            tool_id,
            message_id,
            uid=uid,
            folder=folder or "INBOX",
            preview_only=not full,
        )

    if subcommand == "attachments":
        folder, remainder = executor._parse_flag_value(remainder, "--folder")
        message_id, uid = executor._parse_mail_single_selector(
            remainder,
            usage="Usage: mail attachments [--mailbox <email>] [--folder F] (<id> | --uid <uid>)",
        )
        return await mail_service.list_email_attachments(
            executor.vfs.user,
            tool_id,
            message_id,
            uid=uid,
            folder=folder or "INBOX",
        )

    if subcommand == "folders":
        if remainder:
            raise _terminal_command_error("Usage: mail folders [--mailbox <email>]")
        return await mail_service.list_mailboxes(executor.vfs.user, tool_id)

    if subcommand == "move":
        folder, remainder = executor._parse_flag_value(remainder, "--folder")
        to_folder, remainder = executor._parse_flag_value(remainder, "--to-folder")
        to_special, remainder = executor._parse_flag_value(remainder, "--to-special")
        message_ids, uids = executor._parse_mail_multi_selectors(
            remainder,
            usage=(
                "Usage: mail move [--mailbox <email>] [--folder <src>] <id> [<id> ...] "
                "[--uid <uid> ...] (--to-folder <dest> | --to-special <junk|trash|archive>)"
            ),
        )
        try:
            return await mail_service.move_emails(
                executor.vfs.user,
                tool_id,
                message_ids=message_ids,
                uids=uids,
                source_folder=folder or "INBOX",
                target_folder=to_folder,
                target_special=to_special,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc

    if subcommand == "mark":
        folder, remainder = executor._parse_flag_value(remainder, "--folder")
        actions = {
            "--seen": "seen",
            "--unseen": "unseen",
            "--flagged": "flagged",
            "--unflagged": "unflagged",
        }
        selected_action = None
        filtered: list[str] = []
        for token in remainder:
            if token in actions:
                if selected_action is not None:
                    raise _terminal_command_error(
                        "Usage: mail mark [--mailbox <email>] [--folder <src>] <id> [<id> ...] "
                        "[--uid <uid> ...] (--seen | --unseen | --flagged | --unflagged)"
                    )
                selected_action = actions[token]
            else:
                filtered.append(token)
        message_ids, uids = executor._parse_mail_multi_selectors(
            filtered,
            usage=(
                "Usage: mail mark [--mailbox <email>] [--folder <src>] <id> [<id> ...] "
                "[--uid <uid> ...] (--seen | --unseen | --flagged | --unflagged)"
            ),
        )
        if selected_action is None:
            raise _terminal_command_error(
                "Usage: mail mark [--mailbox <email>] [--folder <src>] <id> [<id> ...] "
                "[--uid <uid> ...] (--seen | --unseen | --flagged | --unflagged)"
            )
        return await mail_service.mark_emails(
            executor.vfs.user,
            tool_id,
            message_ids=message_ids,
            uids=uids,
            folder=folder or "INBOX",
            action=selected_action,
        )

    if subcommand == "import":
        folder, remainder = executor._parse_flag_value(remainder, "--folder")
        attachment_id, remainder = executor._parse_flag_value(remainder, "--attachment")
        output_path, remainder = executor._parse_flag_value(remainder, "--output")
        if not attachment_id:
            raise _terminal_command_error(
                "Usage: mail import [--mailbox <email>] [--folder F] (<id> | --uid <uid>) "
                "--attachment <part> [--output PATH]"
            )
        message_id, uid = executor._parse_mail_single_selector(
            remainder,
            usage=(
                "Usage: mail import [--mailbox <email>] [--folder F] (<id> | --uid <uid>) "
                "--attachment <part> [--output PATH]"
            ),
        )
        try:
            _envelope, _message, resolved_uid, _flags, attachments = await mail_service._load_email_message_with_attachments(
                executor.vfs.user,
                tool_id,
                message_id=message_id,
                uid=uid,
                folder=folder or "INBOX",
            )
        except (ValueError, mail_service.EmailServiceError) as exc:
            raise _terminal_command_error(str(exc)) from exc
        selected = next(
            (item for item in attachments if str(item.get("attachment_id")) == str(attachment_id)),
            None,
        )
        if selected is None:
            selector = uid if uid is not None else message_id if message_id is not None else resolved_uid
            raise _terminal_command_error(f"Attachment {attachment_id} not found on email {selector}.")
        source_name = str(selected.get("filename") or f"attachment-{attachment_id}")
        destination = output_path or posixpath.join(executor.vfs.cwd, source_name)
        try:
            destination = await executor.vfs.resolve_output_path(destination, source_name=source_name)
        except VFSError as exc:
            raise _terminal_command_error(str(exc)) from exc
        written = await executor._write_file_and_notify(
            destination,
            bytes(selected.get("content") or b""),
            mime_type=str(selected.get("mime_type") or "application/octet-stream"),
        )
        return executor._format_write_result(f"Imported attachment to {written.path}", written)

    if subcommand == "send":
        to, remainder = executor._parse_flag_value(remainder, "--to")
        cc, remainder = executor._parse_flag_value(remainder, "--cc")
        subject, remainder = executor._parse_flag_value(remainder, "--subject")
        body_file, remainder = executor._parse_flag_value(remainder, "--body-file")
        attach_paths, remainder = executor._parse_multi_flag(remainder, "--attach")
        if remainder or not to or not subject or not body_file:
            raise _terminal_command_error(
                "Usage: mail send [--mailbox <email>] --to <addr> --subject <subject> "
                "--body-file <path> [--cc <addr>] [--attach <path> ...]"
            )
        if not entry.get("can_send"):
            raise _terminal_command_error(
                f"Sending is disabled for mailbox '{entry['selector_email']}'."
            )
        body = await executor.vfs.read_text(body_file)
        return await executor._send_mail_direct(
            tool_id=tool_id,
            to=to,
            cc=cc,
            subject=subject,
            body=body,
            attach_paths=attach_paths,
        )

    raise _terminal_command_error(f"Unknown mail subcommand: {subcommand}")


async def cmd_calendar(executor: TerminalExecutor, args: list[str]) -> str:
    if not executor.capabilities.has_calendar:
        raise _terminal_command_error("Calendar commands are not enabled for this agent.")
    if not args:
        raise _terminal_command_error(
            "Usage: calendar <accounts|calendars|upcoming|list|search|show|create|update|delete> ..."
        )
    subcommand = args[0]
    remainder = args[1:]
    account, remainder = executor._parse_flag_value(remainder, "--account")

    if subcommand == "accounts":
        if remainder:
            raise _terminal_command_error("Usage: calendar accounts")
        _user, entries, _lookup, selector_values = await executor._get_calendar_registry()
        lines = [executor._format_calendar_accounts(entries)]
        if len(selector_values) > 1:
            lines.append("Pass --account <selector> on calendar commands to choose an account explicitly.")
        return "\n".join(lines)

    entry, _selector_values = await executor._resolve_terminal_calendar_account(account)
    tool_id = int(entry["tool_id"])

    if subcommand == "calendars":
        if remainder:
            raise _terminal_command_error("Usage: calendar calendars [--account <selector>]")
        calendars = await caldav_service.list_calendars(executor.vfs.user, tool_id)
        if not calendars:
            return "No calendars available."
        return "\n".join(["Available calendars:", *[f"- {item}" for item in calendars]])

    if subcommand == "upcoming":
        output_path, remainder = executor._parse_output_path(remainder)
        calendar_name, remainder = executor._parse_flag_value(remainder, "--calendar")
        days_value, remainder = executor._parse_flag_value(remainder, "--days")
        if remainder:
            raise _terminal_command_error(
                "Usage: calendar upcoming [--account <selector>] [--calendar <name>] [--days N] [--output /path.md|json]"
            )
        days = executor._parse_int_flag("--days", days_value or "7")
        try:
            events = await caldav_service.list_events_to_come(
                executor.vfs.user,
                tool_id,
                days_ahead=days,
                calendar_name=calendar_name,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if output_path:
            return await executor._write_calendar_output(
                output_path,
                {"events": events, "days": days, "account": entry["account"], "calendar": calendar_name},
                executor._render_calendar_markdown(heading="Upcoming Events", events=events),
            )
        return executor._format_calendar_event_list(events, heading="Upcoming events:")

    if subcommand == "list":
        output_path, remainder = executor._parse_output_path(remainder)
        calendar_name, remainder = executor._parse_flag_value(remainder, "--calendar")
        start_value, remainder = executor._parse_flag_value(remainder, "--from")
        end_value, remainder = executor._parse_flag_value(remainder, "--to")
        if remainder or not start_value or not end_value:
            raise _terminal_command_error(
                "Usage: calendar list --from <iso> --to <iso> [--account <selector>] [--calendar <name>] [--output /path.md|json]"
            )
        try:
            events = await caldav_service.list_events(
                executor.vfs.user,
                tool_id,
                start_date=start_value,
                end_date=end_value,
                calendar_name=calendar_name,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if output_path:
            return await executor._write_calendar_output(
                output_path,
                {"events": events, "from": start_value, "to": end_value, "account": entry["account"], "calendar": calendar_name},
                executor._render_calendar_markdown(heading="Calendar Events", events=events),
            )
        return executor._format_calendar_event_list(events, heading="Calendar events:")

    if subcommand == "search":
        output_path, remainder = executor._parse_output_path(remainder)
        calendar_name, remainder = executor._parse_flag_value(remainder, "--calendar")
        days_value, remainder = executor._parse_flag_value(remainder, "--days")
        query = " ".join(remainder).strip()
        if not query:
            raise _terminal_command_error(
                "Usage: calendar search <query> [--account <selector>] [--calendar <name>] [--days N] [--output /path.md|json]"
            )
        days = executor._parse_int_flag("--days", days_value or "30")
        try:
            events = await caldav_service.search_events(
                executor.vfs.user,
                tool_id,
                query=query,
                days_range=days,
                calendar_name=calendar_name,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if output_path:
            return await executor._write_calendar_output(
                output_path,
                {"events": events, "query": query, "days": days, "account": entry["account"], "calendar": calendar_name},
                executor._render_calendar_markdown(heading=f"Calendar Search: {query}", events=events),
            )
        return executor._format_calendar_event_list(events, heading="Matching calendar events:")

    if subcommand == "show":
        output_path, remainder = executor._parse_output_path(remainder)
        calendar_name, remainder = executor._parse_flag_value(remainder, "--calendar")
        if len(remainder) != 1:
            raise _terminal_command_error(
                "Usage: calendar show <event-id> [--account <selector>] [--calendar <name>] [--output /path.md|json]"
            )
        try:
            event = await caldav_service.get_event_detail(
                executor.vfs.user,
                tool_id,
                event_id=remainder[0],
                calendar_name=calendar_name,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if output_path:
            return await executor._write_calendar_output(
                output_path,
                {"event": event, "account": entry["account"], "calendar": calendar_name},
                executor._render_calendar_markdown(heading="Calendar Event", event=event),
            )
        return executor._format_calendar_event(event, detailed=True)

    if subcommand == "create":
        calendar_name, remainder = executor._parse_flag_value(remainder, "--calendar")
        title, remainder = executor._parse_flag_value(remainder, "--title")
        start_value, remainder = executor._parse_flag_value(remainder, "--start")
        end_value, remainder = executor._parse_flag_value(remainder, "--end")
        location, remainder = executor._parse_flag_value(remainder, "--location")
        description_file, remainder = executor._parse_flag_value(remainder, "--description-file")
        all_day = "--all-day" in remainder
        remainder = [item for item in remainder if item != "--all-day"]
        if remainder or not calendar_name or not title or not start_value:
            raise _terminal_command_error(
                "Usage: calendar create --title <text> --start <iso> [--end <iso>] [--all-day] --calendar <name> [--account <selector>] [--location <text>] [--description-file /path.md]"
            )
        description = await executor.vfs.read_text(description_file) if description_file else None
        try:
            event = await caldav_service.create_event(
                executor.vfs.user,
                tool_id,
                calendar_name=calendar_name,
                summary=title,
                start=start_value,
                end=end_value,
                all_day=all_day,
                location=location,
                description=description,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc
        return f"Created event {event['uid']} in calendar {event['calendar_name']}"

    if subcommand == "update":
        calendar_name, remainder = executor._parse_flag_value(remainder, "--calendar")
        title, remainder = executor._parse_flag_value(remainder, "--title")
        start_value, remainder = executor._parse_flag_value(remainder, "--start")
        end_value, remainder = executor._parse_flag_value(remainder, "--end")
        location, remainder = executor._parse_flag_value(remainder, "--location")
        description_file, remainder = executor._parse_flag_value(remainder, "--description-file")
        all_day = "--all-day" in remainder
        remainder = [item for item in remainder if item != "--all-day"]
        if len(remainder) != 1 or not calendar_name:
            raise _terminal_command_error(
                "Usage: calendar update <event-id> [--account <selector>] --calendar <name> [--title <text>] [--start <iso>] [--end <iso>] [--all-day] [--location <text>] [--description-file /path.md]"
            )
        description = await executor.vfs.read_text(description_file) if description_file else None
        if not any(
            value is not None
            for value in [title, start_value, end_value, location, description]
        ) and not all_day:
            raise _terminal_command_error("calendar update requires at least one field to change.")
        try:
            event = await caldav_service.update_event(
                executor.vfs.user,
                tool_id,
                event_id=remainder[0],
                calendar_name=calendar_name,
                summary=title,
                start=start_value,
                end=end_value,
                all_day=True if all_day else None,
                location=location,
                description=description,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc
        return f"Updated event {event['uid']} in calendar {event['calendar_name']}"

    if subcommand == "delete":
        calendar_name, remainder = executor._parse_flag_value(remainder, "--calendar")
        confirm = "--confirm" in remainder
        remainder = [item for item in remainder if item != "--confirm"]
        if len(remainder) != 1:
            raise _terminal_command_error(
                "Usage: calendar delete <event-id> [--account <selector>] [--calendar <name>] --confirm"
            )
        if not confirm:
            raise _terminal_command_error("calendar delete requires --confirm")
        try:
            event = await caldav_service.delete_event(
                executor.vfs.user,
                tool_id,
                event_id=remainder[0],
                calendar_name=calendar_name,
            )
        except ValueError as exc:
            raise _terminal_command_error(str(exc)) from exc
        return f"Deleted event {event['uid']} from calendar {event['calendar_name']}"

    raise _terminal_command_error(
        "Usage: calendar <accounts|calendars|upcoming|list|search|show|create|update|delete> ..."
    )


async def cmd_mcp(executor: TerminalExecutor, args: list[str], *, stdin_text: str | None = None, capture_output: bool = False) -> str:
    if not executor.capabilities.has_mcp:
        raise _terminal_command_error("MCP commands are not enabled for this agent.")
    if not args:
        raise _terminal_command_error("Usage: mcp <servers|tools|schema|call|refresh> ...")
    subcommand = args[0]
    remainder = args[1:]
    if subcommand == "servers":
        if remainder:
            raise _terminal_command_error("Usage: mcp servers")
        payload = [
            {
                "id": tool.id,
                "name": tool.name,
                "endpoint": tool.endpoint,
                "transport_type": tool.transport_type,
            }
            for tool in list(executor.capabilities.mcp_tools or [])
        ]
        if capture_output:
            return executor._render_structured_stdout(payload, capture_output=True)
        return executor._format_remote_service_listing("MCP servers", payload)

    if subcommand == "refresh":
        server_selector, remainder = executor._parse_flag_value(remainder, "--server")
        if remainder:
            raise _terminal_command_error("Usage: mcp refresh [--server <selector>]")
        servers = (
            [executor._resolve_remote_tool(
                selector=server_selector,
                tools=list(executor.capabilities.mcp_tools or []),
                noun="MCP server",
                flag_name="--server",
            )]
            if server_selector is not None
            else list(executor.capabilities.mcp_tools or [])
        )
        payload: list[dict[str, Any]] = []
        for server in servers:
            try:
                tools = await mcp_service.list_mcp_tools(
                    tool=server,
                    user=executor.vfs.user,
                    force_refresh=True,
                )
            except mcp_service.MCPServiceError as exc:
                raise _terminal_command_error(str(exc)) from exc
            payload.append({"id": server.id, "name": server.name, "tool_count": len(tools)})
        if capture_output:
            return executor._render_structured_stdout(payload, capture_output=True)
        if len(payload) == 1:
            entry = payload[0]
            return f"Refreshed {entry['name']} ({entry['tool_count']} tools)."
        return "\n".join(
            [f"Refreshed {entry['name']} ({entry['tool_count']} tools)." for entry in payload]
        )

    if subcommand == "tools":
        server_selector, remainder = executor._parse_flag_value(remainder, "--server")
        if remainder:
            raise _terminal_command_error("Usage: mcp tools [--server <selector>]")
        server = executor._resolve_remote_tool(
            selector=server_selector,
            tools=list(executor.capabilities.mcp_tools or []),
            noun="MCP server",
            flag_name="--server",
        )
        try:
            payload = await mcp_service.list_mcp_tools(tool=server, user=executor.vfs.user)
        except mcp_service.MCPServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if capture_output:
            return executor._render_structured_stdout(payload, capture_output=True)
        if not payload:
            return f"No MCP tools discovered on {server.name}."
        lines = [f"Discovered MCP tools on {server.name}:"]
        for item in payload:
            line = f"- {item.get('name')}"
            description = str(item.get("description") or "").strip()
            if description:
                line += f" / {description}"
            lines.append(line)
        return "\n".join(lines)

    if subcommand == "schema":
        server_selector, remainder = executor._parse_flag_value(remainder, "--server")
        output_path, remainder = executor._parse_output_path(remainder)
        if len(remainder) != 1:
            raise _terminal_command_error("Usage: mcp schema <tool-name> [--server <selector>]")
        server = executor._resolve_remote_tool(
            selector=server_selector,
            tools=list(executor.capabilities.mcp_tools or []),
            noun="MCP server",
            flag_name="--server",
        )
        try:
            payload = await mcp_service.describe_mcp_tool(
                tool=server,
                user=executor.vfs.user,
                tool_name=remainder[0],
            )
        except mcp_service.MCPServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if output_path:
            written = await executor._write_json_output(output_path, payload)
            return executor._format_write_result(f"Wrote MCP schema to {written.path}", written)
        return executor._render_structured_stdout(payload, capture_output=capture_output)

    if subcommand == "call":
        server_selector, remainder = executor._parse_flag_value(remainder, "--server")
        extract_to, remainder = executor._parse_flag_value(remainder, "--extract-to")
        output_path, remainder = executor._parse_output_path(remainder)
        if not remainder:
            raise _terminal_command_error(
                "Usage: mcp call <tool-name> [--server <selector>] [--input-file /path.json] "
                "[--output /path.json] [--extract-to /dir]"
            )
        server = executor._resolve_remote_tool(
            selector=server_selector,
            tools=list(executor.capabilities.mcp_tools or []),
            noun="MCP server",
            flag_name="--server",
        )
        tool_name = remainder[0]
        inline_tokens = remainder[1:]
        payload, leftover = await executor._load_command_json_input(
            remaining=inline_tokens,
            stdin_text=stdin_text,
        )
        if leftover:
            raise _terminal_command_error("Unexpected arguments after MCP input payload.")

        try:
            result = await mcp_service.call_mcp_tool(
                tool=server,
                user=executor.vfs.user,
                tool_name=tool_name,
                payload=payload,
            )
        except mcp_service.MCPServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        artifacts = list(result.get("extractable_artifacts") or [])
        if artifacts and not output_path and not extract_to:
            raise _terminal_command_error(
                "This MCP result includes extractable files or resources. Use --output or --extract-to."
            )

        extracted_paths: list[str] = []
        if extract_to:
            try:
                await executor._mkdir_and_notify(extract_to)
            except VFSError:
                pass
            for artifact in artifacts:
                destination = posixpath.join(extract_to, artifact.path)
                written = await executor._write_file_and_notify(
                    destination,
                    artifact.content,
                    mime_type=artifact.mime_type,
                )
                extracted_paths.append(written.path)
        if output_path:
            written = await executor._write_json_output(output_path, result["payload"])
            message = executor._format_write_result(f"Wrote MCP result to {written.path}", written)
            if extracted_paths:
                message += "\nExtracted:\n" + "\n".join(f"- {path}" for path in extracted_paths)
            return message

        if capture_output:
            return executor._render_structured_stdout(result["payload"], capture_output=True)

        rendered = executor._truncate_output(executor._render_mcp_call_interactive(result))
        if extracted_paths:
            rendered += "\nExtracted:\n" + "\n".join(f"- {path}" for path in extracted_paths)
        return rendered

    raise _terminal_command_error("Usage: mcp <servers|tools|schema|call|refresh> ...")


async def cmd_api(executor: TerminalExecutor, args: list[str], *, stdin_text: str | None = None, capture_output: bool = False) -> str:
    if not executor.capabilities.has_api:
        raise _terminal_command_error("API commands are not enabled for this agent.")
    if not args:
        raise _terminal_command_error("Usage: api <services|operations|schema|call> ...")
    subcommand = args[0]
    remainder = args[1:]
    if subcommand == "services":
        if remainder:
            raise _terminal_command_error("Usage: api services")
        payload = [
            {"id": tool.id, "name": tool.name, "endpoint": tool.endpoint}
            for tool in list(executor.capabilities.api_tools or [])
        ]
        if capture_output:
            return executor._render_structured_stdout(payload, capture_output=True)
        return executor._format_remote_service_listing("API services", payload)

    if subcommand == "operations":
        service_selector, remainder = executor._parse_flag_value(remainder, "--service")
        if remainder:
            raise _terminal_command_error("Usage: api operations [--service <selector>]")
        service = executor._resolve_remote_tool(
            selector=service_selector,
            tools=list(executor.capabilities.api_tools or []),
            noun="API service",
            flag_name="--service",
        )
        try:
            payload = await api_tools_service.list_api_operations(tool=service)
        except api_tools_service.APIServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if capture_output:
            return executor._render_structured_stdout(payload, capture_output=True)
        return executor._format_api_operation_listing(payload)

    if subcommand == "schema":
        service_selector, remainder = executor._parse_flag_value(remainder, "--service")
        output_path, remainder = executor._parse_output_path(remainder)
        if len(remainder) != 1:
            raise _terminal_command_error("Usage: api schema <operation> [--service <selector>]")
        service = executor._resolve_remote_tool(
            selector=service_selector,
            tools=list(executor.capabilities.api_tools or []),
            noun="API service",
            flag_name="--service",
        )
        try:
            payload = await api_tools_service.describe_api_operation(
                tool=service,
                operation_selector=remainder[0],
            )
        except api_tools_service.APIServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        if output_path:
            written = await executor._write_json_output(output_path, payload)
            return executor._format_write_result(f"Wrote API schema to {written.path}", written)
        return executor._render_structured_stdout(payload, capture_output=capture_output)

    if subcommand == "call":
        service_selector, remainder = executor._parse_flag_value(remainder, "--service")
        output_path, remainder = executor._parse_output_path(remainder)
        if not remainder:
            raise _terminal_command_error(
                "Usage: api call <operation> [--service <selector>] [--input-file /path.json] "
                "[--output /path.json|/path.txt|/path.bin]"
            )
        service = executor._resolve_remote_tool(
            selector=service_selector,
            tools=list(executor.capabilities.api_tools or []),
            noun="API service",
            flag_name="--service",
        )
        operation_selector = remainder[0]
        inline_tokens = remainder[1:]
        payload, leftover = await executor._load_command_json_input(
            remaining=inline_tokens,
            stdin_text=stdin_text,
        )
        if leftover:
            raise _terminal_command_error("Unexpected arguments after API input payload.")

        try:
            result = await api_tools_service.call_api_operation(
                tool=service,
                user=executor.vfs.user,
                operation_selector=operation_selector,
                payload=payload,
            )
        except api_tools_service.APIServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc

        if output_path:
            if result["body_kind"] == "binary":
                try:
                    resolved_output = await executor.vfs.resolve_output_path(
                        output_path,
                        source_name=str(result.get("filename") or "response.bin"),
                    )
                except VFSError as exc:
                    raise _terminal_command_error(str(exc)) from exc
                written = await executor._write_file_and_notify(
                    resolved_output,
                    result["binary_content"],
                    mime_type=result["content_type"],
                )
                return executor._format_write_result(f"Wrote API response to {written.path}", written)
            if result["body_kind"] == "json" and result["payload"]["response"].get("json") is not None:
                written = await executor._write_json_output(output_path, result["payload"]["response"]["json"])
                return executor._format_write_result(f"Wrote API response to {written.path}", written)
            written = await executor._write_text_output(
                output_path,
                str(result["payload"]["response"].get("text") or ""),
                mime_type="text/plain",
            )
            return executor._format_write_result(f"Wrote API response to {written.path}", written)

        if result["body_kind"] == "binary":
            if capture_output:
                raise _terminal_command_error(
                    "Binary API responses cannot be piped or redirected without --output."
                )
            return (
                f"Binary API response ({result['content_type']}, {len(result['binary_content'])} bytes). "
                "Use --output to save it."
            )

        if capture_output:
            return executor._render_structured_stdout(result["payload"], capture_output=True)
        return executor._truncate_output(executor._render_api_call_interactive(result))

    raise _terminal_command_error("Usage: api <services|operations|schema|call> ...")
