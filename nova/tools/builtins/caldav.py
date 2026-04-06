import copy
import logging
from datetime import datetime, timedelta, timezone
from icalendar import Event as iCalEvent
from typing import Optional, List

from asgiref.sync import sync_to_async  # For async-safe ORM accesses
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _, ngettext

from langchain_core.tools import StructuredTool

from nova.caldav import service as caldav_service
from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool
from nova.tools.multi_instance import (
    build_selector_schema,
)


logger = logging.getLogger(__name__)


async def get_caldav_client(user, tool_id):
    return await caldav_service.get_caldav_client(user, tool_id)


async def list_calendars(user, tool_id) -> str:
    """ Get a list of available calendars.
    Args:
        user: the Django user
        tool_id: ID of the CalDav tool

    Returns:
        Formatted list of calendars
    """
    calendars = await caldav_service.list_calendars(user, tool_id)

    if not calendars:
        return _("No calendars available.")

    result = _("Available calendars :\n")
    for cal in calendars:
        result += f"- {cal}\n"

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


def _format_event_payload(payload: dict) -> str:
    lines = [f"Event name :{payload.get('summary') or ''}"]
    description = str(payload.get("description") or "").strip()
    if description:
        lines.append(f"Event description :{description}")
    lines.append(f"Start : {payload.get('start') or ''}")
    if payload.get("end"):
        lines.append(f"End : {payload['end']}")
    else:
        lines.append("End date is not set")
    if payload.get("location"):
        lines.append(f"Location : {payload['location']}")
    if payload.get("uid"):
        lines.append(f"UID : {payload['uid']}")
    return "\n".join(lines)


def _describe_normalized_events(events: list[dict]) -> list[str]:
    return [_format_event_payload(payload) for payload in events]


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
    events = await caldav_service.list_events_to_come(
        user,
        tool_id,
        days_ahead=days_ahead,
        calendar_name=calendar_name,
    )
    if not events:
        return _("No events found")
    return str(_describe_normalized_events(events))


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
    try:
        events = await caldav_service.list_events(
            user,
            tool_id,
            start_date=start_date,
            end_date=end_date,
            calendar_name=calendar_name,
        )
    except ValueError as exc:
        return str(exc)
    if not events:
        return _("No events found")
    return str(_describe_normalized_events(events))


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
    try:
        payload = await caldav_service.get_event_detail(
            user,
            tool_id,
            event_id=event_id,
            calendar_name=calendar_name,
        )
    except ValueError as exc:
        return str(exc)
    return _format_event_payload(payload)


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
    try:
        events = await caldav_service.search_events(
            user,
            tool_id,
            query=query,
            days_range=days_range,
        )
    except Exception as exc:
        error_message = _("Error when searching events : {}")
        return error_message.format(exc)
    return str(_describe_normalized_events(events))


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


async def _build_calendar_registry(tools: list[Tool], agent: LLMAgent) -> tuple:
    user, entries, lookup, selector_values = await caldav_service.build_calendar_registry(tools, agent)
    selector_schema = build_selector_schema(
        selector_name="calendar_account",
        labels=selector_values,
        description=(
            "CalDAV account identifier to use. "
            f"Available accounts: {', '.join(selector_values)}."
        ),
    )
    normalized_entries = [
        {
            **entry,
            "calendar_account": entry.get("account"),
        }
        for entry in entries
    ]
    return user, normalized_entries, lookup, selector_schema, selector_values


def _resolve_calendar_account(
    calendar_account: str,
    lookup: dict[str, list[dict]],
    selector_values: List[str],
) -> tuple:
    return caldav_service.resolve_calendar_account(calendar_account, lookup, selector_values)


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
    user = await caldav_service._resolve_user_for_tool(tool, agent)
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
