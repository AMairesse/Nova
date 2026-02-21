# nova/tools/builtins/caldav.py
import caldav
import copy
import logging
import re
from datetime import datetime, timedelta, timezone
from icalendar import Event as iCalEvent
from typing import Optional, List

from asgiref.sync import sync_to_async  # For async-safe ORM accesses
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _, ngettext

from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool, ToolCredential
from nova.tools.multi_instance import (
    build_selector_schema,
    dedupe_instance_labels,
    format_invalid_instance_message,
    normalize_instance_key,
)


logger = logging.getLogger(__name__)
EMAIL_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


async def get_caldav_client(user, tool_id):
    try:
        # Wrap ORM access in sync_to_async
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
        caldav_url = credential.config.get('caldav_url')
        username = credential.config.get('username')
        password = credential.config.get('password')

        if not all([caldav_url, username, password]):
            raise ValueError(_("Incomplete CalDav configuration: missing URL, username, or password"))

        client = caldav.DAVClient(
            url=caldav_url,
            username=username,
            password=password
        )

        # Test auth (caldav is sync, but safe in async context)
        try:
            client.principal()  # Early auth check
        except caldav.lib.error.AuthorizationError as e:
            raise ValueError(f"CalDav authorization failed: {str(e)}")
        except Exception as e:
            raise ConnectionError(f"CalDav connection failed: {str(e)}")

        return client

    except ToolCredential.DoesNotExist:
        raise ValueError(_("No CalDav credential found for tool {tool_id}").format(tool_id=tool_id))
    except Exception as e:  # Catch-all
        logger.error(f"CalDav client error: {str(e)}")
        raise


async def list_calendars(user, tool_id) -> str:
    """ Get a list of available calendars.
    Args:
        user: the Django user
        tool_id: ID of the CalDav tool

    Returns:
        Formatted list of calendars
    """
    client = await get_caldav_client(user, tool_id)
    principal = client.principal()
    calendars = principal.calendars()

    if not calendars:
        return _("No calendars available.")

    result = _("Available calendars :\n")
    for cal in calendars:
        result += f"- {cal.name}\n"

    return result


def describe_events(events: List[iCalEvent]) -> List[str]:
    # Generate a list of strings containing the events
    all_events = []
    for event in events:
        for component in event.icalendar_instance.walk():
            if component.name != "VEVENT":
                continue
            try:
                event_str = "Event name :" + component.get("summary") + "\n"
                description = component.get("description")
                if description:
                    event_str += "Event description :" + description + "\n"
                event_str += "Start : " + component.get("dtstart").dt.strftime('%Y-%m-%d %Hh%M') + "\n"
                endDate = component.get("dtend")
                if endDate and endDate.dt:
                    event_str += "End : " + endDate.dt.strftime('%Y-%m-%d %Hh%M') + "\n"
                else:
                    event_str += "End date is not set" + "\n"
                if component.get("location"):
                    event_str += "Location : " + component.get("location") + "\n"
                if component.get("UID"):
                    event_str += "UID : " + component.get("UID")
                all_events.append(event_str)
            except Exception as e:
                logger.error(f"Error when processing event: {str(e)}")
                continue

    return all_events


async def list_events_to_come(user, tool_id, days_ahead: int = 7, calendar_name: Optional[str] = None) -> str:
    """ List events for the next days_ahead.
    Args:
        user: the Django user
        tool_id: ID of the CalDav tool
        days_ahead: number of days to look ahead (default: 7)
        calendar_name: calendar's name (optional)

    Returns:
        Formatted list of events
    """
    start_date = datetime.now(timezone.utc)
    end_date = start_date + timedelta(days=days_ahead)

    return await list_events(user, tool_id, start_date.strftime('%Y-%m-%d'),
                             end_date.strftime('%Y-%m-%d'), calendar_name)


async def list_events(user, tool_id, start_date: str, end_date: str, calendar_name: Optional[str] = None) -> str:
    """ List events between start_date and end_date.
    Args:
        user: the Django user
        tool_id: ID of the CalDav tool
        start_date: start of the period (format: YYYY-MM-DD)
        end_date: end of the period (format: YYYY-MM-DD)
        calendar_name: calendar's name (optional)

    Returns:
        Formatted list of events between start_date and end_date
    """
    client = await get_caldav_client(user, tool_id)
    principal = client.principal()

    if calendar_name:
        calendars = [cal for cal in principal.calendars() if cal.name == calendar_name]
        if not calendars:
            return _("Calendar '{calendar_name}' not found.").format(calendar_name=calendar_name)
    else:
        calendars = principal.calendars()

    if not calendars:
        return _("No calendars available.")

    # Define search period
    start_date = datetime.strptime(start_date, '%Y-%m-%d')
    end_date = datetime.strptime(end_date, '%Y-%m-%d')
    # Set start_date to the beginning of the day
    start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)

    # Set end_date to the end of the day
    end_date = end_date.replace(hour=23, minute=59, second=59, microsecond=999999)

    all_events = []

    for cal in calendars:
        events_fetched = cal.search(start=start_date, end=end_date, event=True, expand=True)
        all_events.extend(describe_events(events_fetched))

    # If the list is empty, return a message for the LLM
    if not all_events:
        all_events.append(_("No events found"))
    return str(all_events)


async def get_event_detail(user, tool_id, event_id: str, calendar_name: Optional[str] = None) -> str:
    """ Get an event's details.
    Args:
        user: the Django user
        tool_id: ID of the CalDav tool
        event_id: UID of the event
        calendar_name: calendar's name (optional)

    Returns:
        A string containing the event's details
    """
    client = await get_caldav_client(user, tool_id)
    principal = client.principal()

    if calendar_name:
        calendars = [cal for cal in principal.calendars() if cal.name == calendar_name]
        if not calendars:
            return _("Calendar '{calendar_name}' not found.").format(calendar_name=calendar_name)
    else:
        calendars = principal.calendars()

    if not calendars:
        return _("No calendars available.")

    for calendar in calendars:
        event = calendar.search(uid=event_id, event=True, expand=False)
        if event:
            return str(event)
    return _("Event not found.")


async def search_events(user, tool_id, query: str, days_range: int = 30) -> str:
    """ Search for events containing the query.
    Args:
        user: the Django user
        tool_id: ID of the CalDav tool
        query: text to search
        days_range: number of days to search (past and future, default: 30)

    Returns:
        Formatted list of events
    """
    client = await get_caldav_client(user, tool_id)
    principal = client.principal()
    calendars = principal.calendars()

    if not calendars:
        return _("No calendars available.")

    # Define search period
    start_date = datetime.now(timezone.utc) - timedelta(days=days_range)
    end_date = datetime.now(timezone.utc) + timedelta(days=days_range)

    matching_events = []

    for calendar in calendars:
        try:
            # TODO: filter on summary seems to be broken, to investigate
            events = calendar.search(start=start_date, end=end_date,
                                     summary=query,
                                     event=True, expand=True)
            matching_events.extend(describe_events(events))
        except Exception as e:
            error_message = _("Error when searching events : {}")
            return error_message.format(e)
    return str(matching_events)


async def test_caldav_access(user, tool_id):
    result = await list_calendars(user, tool_id)

    if "error" in result.lower():
        return JsonResponse({"status": "error", "message": result})
    else:
        # Response varies depending on the number of calendars
        calendar_count = result.count("- ")
        if calendar_count == 0:
            return {"status": "success", "message": _("No calendars found")}
        else:
            return {
                "status": "success",
                "message": ngettext(
                    "%(count)d calendar found",
                    "%(count)d calendars found",
                    calendar_count
                ) % {"count": calendar_count}
            }


METADATA = {
    'name': 'CalDav',
    'description': 'Interact with a CalDav server (calendars)',
    'loading': {
        'mode': 'skill',
        'skill_id': 'caldav',
        'skill_label': 'CalDav',
    },
    'requires_config': True,
    'config_fields': [
        {'name': 'caldav_url', 'type': 'url', 'label': _('URL CalDav'), 'required': True},
        {'name': 'username', 'type': 'text', 'label': _('Username'), 'required': True},
        {'name': 'password', 'type': 'password', 'label': _('Password'), 'required': True},
    ],
    'test_function': 'test_caldav_access',
    'test_function_args': ['user', 'tool_id'],
}


AGGREGATION_SPEC = {
    "min_instances": 2,
}


def get_skill_instructions(agent=None, tools=None) -> List[str]:
    return [
        "Call list_calendars first to confirm exact calendar names before listing or searching events.",
        "For planning checks, start with list_events_to_come, then narrow with list_events or get_event_detail.",
        "When date ranges are unclear, ask for missing dates instead of guessing a wide period.",
    ]


_CALDAV_TOOL_SPECS = [
    (
        "list_calendars",
        "List all available calendars",
        {
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    (
        "list_events_to_come",
        "List events for the next days_ahead.",
        {
            "type": "object",
            "properties": {
                "days_ahead": {
                    "type": "integer",
                    "description": "number of days to look ahead",
                    "default": 7,
                },
                "calendar_name": {
                    "type": "string",
                    "description": "calendar's name",
                },
            },
            "required": [],
        },
    ),
    (
        "list_events",
        "List events between start_date and end_date.",
        {
            "type": "object",
            "properties": {
                "start_date": {
                    "type": "string",
                    "description": "start of the period (format: YYYY-MM-DD)",
                },
                "end_date": {
                    "type": "string",
                    "description": "end of the period (format: YYYY-MM-DD)",
                },
                "calendar_name": {
                    "type": "string",
                    "description": "calendar's name",
                },
            },
            "required": ["start_date", "end_date"],
        },
    ),
    (
        "get_event_detail",
        "Get an event's details.",
        {
            "type": "object",
            "properties": {
                "event_id": {
                    "type": "string",
                    "description": "UID of the event",
                },
                "calendar_name": {
                    "type": "string",
                    "description": "calendar's name",
                },
            },
            "required": ["event_id"],
        },
    ),
    (
        "search_events",
        "Search for events containing the query",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "text to search",
                },
                "days_range": {
                    "type": "integer",
                    "description": "number of days to search (past and future)",
                    "default": 30,
                },
            },
            "required": ["query"],
        },
    ),
]


def _with_calendar_account_selector(args_schema: dict, selector_schema: dict | None) -> dict:
    schema = copy.deepcopy(args_schema)
    if not selector_schema:
        return schema

    properties = dict(schema.get("properties") or {})
    schema["properties"] = {"calendar_account": copy.deepcopy(selector_schema), **properties}
    required = list(schema.get("required") or [])
    if "calendar_account" not in required:
        required.insert(0, "calendar_account")
    schema["required"] = required
    return schema


def _build_toolset(*, wrappers: dict[str, object], selector_schema: dict | None = None) -> List[StructuredTool]:
    result: List[StructuredTool] = []
    for name, description, args_schema in _CALDAV_TOOL_SPECS:
        result.append(
            StructuredTool.from_function(
                coroutine=wrappers[name],
                name=name,
                description=description,
                args_schema=_with_calendar_account_selector(args_schema, selector_schema),
            )
        )
    return result


async def _resolve_user_for_tool(tool: Tool, agent: LLMAgent | None):
    user = getattr(agent, "user", None) if agent else None
    if user:
        return user
    return await sync_to_async(lambda: tool.user, thread_sensitive=False)()


async def _get_credential(user, tool_id: int) -> ToolCredential | None:
    try:
        return await sync_to_async(
            ToolCredential.objects.get,
            thread_sensitive=False,
        )(user=user, tool_id=tool_id)
    except ToolCredential.DoesNotExist:
        return None


def _extract_email_address(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = EMAIL_ADDRESS_RE.search(text)
    return match.group(0).strip() if match else ""


def _select_calendar_account(config: dict) -> str:
    username = str(config.get("username") or "").strip()
    email = _extract_email_address(username)
    return email or username


def _build_calendar_display_label(alias: str | None, calendar_account: str) -> str:
    cleaned = str(alias or "").strip()
    if not cleaned:
        return ""
    if normalize_instance_key(cleaned) == normalize_instance_key(calendar_account):
        return ""
    return cleaned


async def _build_calendar_registry(tools: list[Tool], agent: LLMAgent) -> tuple:
    if not tools:
        raise ValueError("No CalDAV tools provided for aggregation.")

    user = getattr(agent, "user", None) or getattr(tools[0], "user", None)
    if not user:
        raise ValueError("Cannot resolve user for aggregated CalDAV tools.")

    raw_aliases = [((tool.name or "").strip() or "CalDav") for tool in tools]
    deduped_aliases = dedupe_instance_labels(raw_aliases, default_label="CalDav")

    entries = []
    for tool, base_alias, alias in zip(tools, raw_aliases, deduped_aliases):
        tool_id = getattr(tool, "id", None)
        if not tool_id:
            continue

        if alias != base_alias:
            logger.warning(
                "Duplicate or empty CalDAV alias '%s' detected for tool_id=%s; using '%s'.",
                base_alias,
                tool_id,
                alias,
            )

        credential = await _get_credential(user, tool_id)
        if not credential:
            logger.warning(
                "Skipping CalDAV alias '%s' (tool_id=%s): no credential configured for user_id=%s.",
                alias,
                tool_id,
                getattr(user, "id", "unknown"),
            )
            continue

        config = credential.config or {}
        if not all([config.get("caldav_url"), config.get("username"), config.get("password")]):
            logger.warning(
                "Skipping CalDAV alias '%s' (tool_id=%s): incomplete CalDAV configuration.",
                alias,
                tool_id,
            )
            continue

        calendar_account = _select_calendar_account(config)
        if not calendar_account:
            logger.warning(
                "Skipping CalDAV alias '%s' (tool_id=%s): could not derive calendar account.",
                alias,
                tool_id,
            )
            continue

        entries.append(
            {
                "alias": alias,
                "tool_id": tool_id,
                "calendar_account": calendar_account,
            }
        )

    if not entries:
        raise ValueError("No configured CalDAV account available for aggregation.")

    for entry in entries:
        entry["display_label"] = _build_calendar_display_label(
            entry.get("alias"),
            str(entry.get("calendar_account") or ""),
        )

    selector_values: List[str] = []
    for entry in entries:
        selector = str(entry.get("calendar_account") or "").strip()
        if selector and selector not in selector_values:
            selector_values.append(selector)

    lookup: dict[str, list[dict]] = {}
    for entry in entries:
        key = normalize_instance_key(entry.get("calendar_account"))
        if key:
            lookup.setdefault(key, []).append(entry)

    selector_schema = build_selector_schema(
        selector_name="calendar_account",
        labels=selector_values,
        description=(
            "CalDAV account identifier to use. "
            f"Available accounts: {', '.join(selector_values)}."
        ),
    )
    return user, entries, lookup, selector_schema, selector_values


def _resolve_calendar_account(
    calendar_account: str,
    lookup: dict[str, list[dict]],
    selector_values: List[str],
) -> tuple:
    normalized = normalize_instance_key(calendar_account)
    matches = lookup.get(normalized, [])
    if len(matches) == 1:
        return matches[0], None

    if len(matches) > 1:
        requested = str(calendar_account or "").strip() or "<empty>"
        return None, (
            f"Ambiguous calendar_account '{requested}'. Multiple CalDAV tools share this identifier. "
            "Use a unique calendar_account value."
        )

    return None, format_invalid_instance_message(
        selector_name="calendar_account",
        value=calendar_account,
        available_labels=selector_values,
    )


async def get_aggregated_functions(tools: list[Tool], agent: LLMAgent) -> List[StructuredTool]:
    user, _, lookup, selector_schema, selector_values = await _build_calendar_registry(tools, agent)

    async def list_calendars_wrapper(calendar_account: str) -> str:
        entry, err = _resolve_calendar_account(calendar_account, lookup, selector_values)
        if err:
            return err
        return await list_calendars(user, entry["tool_id"])

    async def list_events_to_come_wrapper(
        calendar_account: str,
        days_ahead: int = 7,
        calendar_name: str = None,
    ) -> str:
        entry, err = _resolve_calendar_account(calendar_account, lookup, selector_values)
        if err:
            return err
        return await list_events_to_come(user, entry["tool_id"], days_ahead, calendar_name)

    async def list_events_wrapper(
        calendar_account: str,
        start_date: str,
        end_date: str,
        calendar_name: str = None,
    ) -> str:
        entry, err = _resolve_calendar_account(calendar_account, lookup, selector_values)
        if err:
            return err
        return await list_events(user, entry["tool_id"], start_date, end_date, calendar_name)

    async def get_event_detail_wrapper(
        calendar_account: str,
        event_id: str,
        calendar_name: str = None,
    ) -> str:
        entry, err = _resolve_calendar_account(calendar_account, lookup, selector_values)
        if err:
            return err
        return await get_event_detail(user, entry["tool_id"], event_id, calendar_name)

    async def search_events_wrapper(
        calendar_account: str,
        query: str,
        days_range: int = 30,
    ) -> str:
        entry, err = _resolve_calendar_account(calendar_account, lookup, selector_values)
        if err:
            return err
        return await search_events(user, entry["tool_id"], query, days_range)

    wrappers = {
        "list_calendars": list_calendars_wrapper,
        "list_events_to_come": list_events_to_come_wrapper,
        "list_events": list_events_wrapper,
        "get_event_detail": get_event_detail_wrapper,
        "search_events": search_events_wrapper,
    }
    return _build_toolset(wrappers=wrappers, selector_schema=selector_schema)


async def get_aggregated_prompt_instructions(tools: list[Tool], agent: LLMAgent) -> List[str]:
    try:
        _, entries, _, _, _ = await _build_calendar_registry(tools, agent)
    except Exception as e:
        logger.warning("Could not build aggregated CalDAV prompt instructions: %s", str(e))
        return []

    account_parts = []
    for entry in entries:
        label = str(entry.get("display_label") or "").strip()
        label_part = f", label: {label}" if label else ""
        account_parts.append(f"{entry['calendar_account']}{label_part}")

    return [
        f"CalDAV account map: {'; '.join(account_parts)}.",
        "Use calendar_account values exactly as listed when calling CalDAV tools.",
    ]


async def get_functions(tool: Tool, agent: LLMAgent) -> List[StructuredTool]:
    """
    Return a list of StructuredTool instances for the available functions,
    with user and id injected via partial.
    """
    # Wrap ORM check in sync_to_async
    has_required_data = await sync_to_async(lambda: bool(tool and tool.id), thread_sensitive=False)()
    if not has_required_data:
        raise ValueError("Tool instance missing required data (user or id).")

    # Wrap ORM accesses for user/id
    user = await _resolve_user_for_tool(tool, agent)
    if not user:
        raise ValueError("Tool instance missing required data (user).")
    tool_id = await sync_to_async(lambda: tool.id, thread_sensitive=False)()

    # Create wrapper functions as langchain 1.1 does not support partial() anymore
    async def list_calendars_wrapper() -> str:
        return await list_calendars(user, tool_id)

    async def list_events_to_come_wrapper(days_ahead: int = 7, calendar_name: str = None) -> str:
        return await list_events_to_come(user, tool_id, days_ahead, calendar_name)

    async def list_events_wrapper(start_date: str, end_date: str, calendar_name: str = None) -> str:
        return await list_events(user, tool_id, start_date, end_date, calendar_name)

    async def get_event_detail_wrapper(event_id: str, calendar_name: str = None) -> str:
        return await get_event_detail(user, tool_id, event_id, calendar_name)

    async def search_events_wrapper(query: str, days_range: int = 30) -> str:
        return await search_events(user, tool_id, query, days_range)

    wrappers = {
        "list_calendars": list_calendars_wrapper,
        "list_events_to_come": list_events_to_come_wrapper,
        "list_events": list_events_wrapper,
        "get_event_detail": get_event_detail_wrapper,
        "search_events": search_events_wrapper,
    }
    return _build_toolset(wrappers=wrappers)
