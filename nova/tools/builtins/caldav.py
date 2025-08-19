from datetime import datetime, timedelta, timezone
from typing import Optional, List
from functools import partial
import caldav
from icalendar import Event as iCalEvent
from django.http import JsonResponse
from django.utils.translation import gettext_lazy as _, ngettext
from nova.models.models import ToolCredential, Tool
from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import LLMAgent

from asgiref.sync import sync_to_async  # For async-safe ORM accesses

import logging
logger = logging.getLogger(__name__)


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
    try:
        client = await get_caldav_client(user, tool_id)
        principal = client.principal()
        calendars = principal.calendars()

        if not calendars:
            return _("No calendars available.")

        result = _("Available calendars :\n")
        for cal in calendars:
            result += f"- {cal.name}\n"

        return result

    except (ValueError, ConnectionError) as e:  # Specific handling
        return _("CalDav error: {error}. Check credentials and server URL.").format(error=str(e))
    except Exception as e:
        return _("Unexpected error when retrieving calendars: {error}").format(error=str(e))


def describe_events(events: List[iCalEvent]) -> List[str]:
    # Generate a list of strings containing the events
    all_events = []
    for event in events:
        for component in event.icalendar_instance.walk():
            if component.name != "VEVENT":
                continue
            try :
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
            except:
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
    try:
        start_date = datetime.now(timezone.utc)
        end_date = start_date + timedelta(days=days_ahead)

        return await list_events(user, tool_id, start_date.strftime('%Y-%m-%d'),
                                 end_date.strftime('%Y-%m-%d'), calendar_name)

    except Exception as e:
        error_message = _("Error when retrieving events : {}")
        return error_message.format(e)


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

    except Exception as e:
        error_message = _("Error when retrieving events : {}")
        return error_message.format(e)


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

    except Exception as e:
        error_message = _("Error when retrieving event's details : {}")
        return error_message.format(e)


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

    except Exception as e:
        error_message = _("Error when searching events : {}")
        return error_message.format(e)


async def test_caldav_access(user, tool_id):
    try:
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
    except Exception as e:
        return {
            "status": "error",
            "message": _("Connection error: %(err)s") % {"err": e}
        }


METADATA = {
    'name': 'CalDav',
    'description': 'Interact with a CalDav server (calendars)',
    'requires_config': True,
    'config_fields': [
        {'name': 'caldav_url', 'type': 'url', 'label': _('URL CalDav'), 'required': True},
        {'name': 'username', 'type': 'text', 'label': _('Username'), 'required': True},
        {'name': 'password', 'type': 'password', 'label': _('Password'), 'required': True},
    ],
    'test_function': test_caldav_access,
    'test_function_args': ['user', 'tool_id'],
}


async def get_functions(tool: Tool, agent: LLMAgent) -> List[StructuredTool]:
    """
    Return a list of StructuredTool instances for the available functions,
    with user and id injected via partial.
    """
    # Wrap ORM check in sync_to_async
    has_required_data = await sync_to_async(lambda: bool(tool and tool.user and tool.id), thread_sensitive=False)()
    if not has_required_data:
        raise ValueError("Tool instance missing required data (user or id).")

    # Wrap ORM accesses for user/id
    user = await sync_to_async(lambda: tool.user, thread_sensitive=False)()
    tool_id = await sync_to_async(lambda: tool.id, thread_sensitive=False)()

    return [
        StructuredTool.from_function(
            coroutine=partial(list_calendars, user, tool_id),
            name="list_calendars",
            description="List all available calendars",
            args_schema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        StructuredTool.from_function(
            coroutine=partial(list_events_to_come, user, tool_id),
            name="list_events_to_come",
            description="List events for the next days_ahead.",
            args_schema={
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "number of days to look ahead",
                        "default": 7
                    },
                    "calendar_name": {
                        "type": "string",
                        "description": "calendar's name"
                    }
                },
                "required": []
            }
        ),
        StructuredTool.from_function(
            coroutine=partial(list_events, user, tool_id),
            name="list_events",
            description="List events between start_date and end_date.",
            args_schema={
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "start of the period (format: YYYY-MM-DD)",
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
                    },
                    "end_date": {
                        "type": "string",
                        "description": "end of the period (format: YYYY-MM-DD)",
                        "pattern": "^\\d{4}-\\d{2}-\\d{2}$"
                    },
                    "calendar_name": {
                        "type": "string",
                        "description": "calendar's name"
                    }
                },
                "required": ["start_date", "end_date"]
            }
        ),
        StructuredTool.from_function(
            coroutine=partial(get_event_detail, user, tool_id),
            name="get_event_detail",
            description="Get en event's details.",
            args_schema={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "UID of the event"
                    },
                    "calendar_name": {
                        "type": "string",
                        "description": "calendar's name"
                    }
                },
                "required": ["event_id"]
            }
        ),
        StructuredTool.from_function(
            coroutine=partial(search_events, user, tool_id),
            name="search_events",
            description="Search for events containing the query",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "text to search"
                    },
                    "days_range": {
                        "type": "integer",
                        "description": "number of days to search (past and future)",
                        "default": 30
                    }
                },
                "required": ["query"]
            }
        )
    ]
