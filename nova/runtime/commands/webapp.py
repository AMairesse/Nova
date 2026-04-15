from __future__ import annotations

from typing import TYPE_CHECKING

from nova.webapp import service as webapp_service

if TYPE_CHECKING:
    from nova.runtime.terminal import TerminalExecutor


def _terminal_command_error(*args, **kwargs):
    from nova.runtime.terminal import TerminalCommandError

    return TerminalCommandError(*args, **kwargs)


async def cmd_webapp(executor: TerminalExecutor, args: list[str]) -> str:
    if not executor.capabilities.has_webapp:
        raise _terminal_command_error("Webapp commands are not enabled for this agent.")
    if not args:
        raise _terminal_command_error("Usage: webapp <list|expose|show|delete> ...")

    subcommand = str(args[0] or "").strip().lower()
    remainder = args[1:]

    if subcommand == "list":
        if remainder:
            raise _terminal_command_error("Usage: webapp list")
        items = await webapp_service.list_thread_webapps(user=executor.vfs.user, thread=executor.vfs.thread)
        return executor._format_webapp_listing(items)

    if subcommand == "show":
        if len(remainder) != 1:
            raise _terminal_command_error("Usage: webapp show <slug>")
        try:
            payload = await webapp_service.describe_webapp(
                user=executor.vfs.user,
                thread=executor.vfs.thread,
                slug=remainder[0],
            )
        except webapp_service.WebAppServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        return executor._format_webapp_details(payload)

    if subcommand == "delete":
        confirm = "--confirm" in remainder
        remainder = [item for item in remainder if item != "--confirm"]
        if len(remainder) != 1:
            raise _terminal_command_error("Usage: webapp delete <slug> --confirm")
        if not confirm:
            raise _terminal_command_error("webapp delete requires --confirm")
        try:
            payload = await webapp_service.delete_webapp(
                user=executor.vfs.user,
                thread=executor.vfs.thread,
                slug=remainder[0],
                task_id=executor.realtime_task_id,
                channel_layer=executor.realtime_channel_layer,
            )
        except webapp_service.WebAppServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        return f"Deleted webapp {payload['slug']}"

    if subcommand == "expose":
        slug, remainder = executor._parse_flag_value(remainder, "--slug")
        name, remainder = executor._parse_flag_value(remainder, "--name")
        entry_path, remainder = executor._parse_flag_value(remainder, "--entry")
        if len(remainder) != 1:
            raise _terminal_command_error(
                "Usage: webapp expose <source_dir> [--name <display-name>] [--entry <relative-path>] "
                "[--slug <slug>]"
            )
        try:
            payload = await webapp_service.expose_webapp(
                user=executor.vfs.user,
                thread=executor.vfs.thread,
                vfs=executor.vfs,
                source_root=remainder[0],
                slug=slug,
                name=name,
                entry_path=entry_path,
                task_id=executor.realtime_task_id,
                channel_layer=executor.realtime_channel_layer,
            )
        except webapp_service.WebAppServiceError as exc:
            raise _terminal_command_error(str(exc)) from exc
        action = "Exposed" if payload.get("created") else "Updated"
        return (
            f"{action} webapp {payload['slug']} at {payload['public_url']} "
            f"from {payload['source_root']} (entry {payload['entry_path']})"
        )

    raise _terminal_command_error("Usage: webapp <list|expose|show|delete> ...")
