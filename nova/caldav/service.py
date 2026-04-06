from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Optional

import caldav
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from icalendar import Calendar as ICalendar
from icalendar import Event as ICalEvent

from nova.models.Tool import Tool, ToolCredential
from nova.tools.multi_instance import (
    dedupe_instance_labels,
    format_invalid_instance_message,
    normalize_instance_key,
)

logger = logging.getLogger(__name__)
EMAIL_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _to_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_iso_input(value: str) -> str:
    return str(value or "").strip().replace("Z", "+00:00")


def _serialize_temporal(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt_value = value
        if timezone.is_naive(dt_value):
            dt_value = timezone.make_aware(dt_value, timezone.get_current_timezone())
        return dt_value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _parse_temporal_input(value: str, *, all_day: bool) -> date | datetime:
    raw = _normalize_iso_input(value)
    if not raw:
        raise ValueError("Date/time value is required.")
    if all_day:
        try:
            parsed_dt = datetime.fromisoformat(raw)
            return parsed_dt.date()
        except ValueError:
            return date.fromisoformat(raw)

    try:
        parsed_dt = datetime.fromisoformat(raw)
    except ValueError:
        parsed_date = date.fromisoformat(raw)
        parsed_dt = datetime.combine(parsed_date, time.min)
    if timezone.is_naive(parsed_dt):
        parsed_dt = timezone.make_aware(parsed_dt, timezone.get_current_timezone())
    return parsed_dt


def _parse_range_boundary_input(value: str, *, is_end: bool) -> datetime:
    raw = _normalize_iso_input(value)
    if not raw:
        raise ValueError("Date/time value is required.")
    if "T" in raw:
        parsed_dt = datetime.fromisoformat(raw)
        if timezone.is_naive(parsed_dt):
            parsed_dt = timezone.make_aware(parsed_dt, timezone.get_current_timezone())
        return parsed_dt
    parsed_date = date.fromisoformat(raw)
    base_time = time.max if is_end else time.min
    parsed_dt = datetime.combine(parsed_date, base_time)
    return timezone.make_aware(parsed_dt, timezone.get_current_timezone())


def _select_calendar_account(config: dict[str, Any]) -> str:
    username = _to_text(config.get("username"))
    match = EMAIL_ADDRESS_RE.search(username)
    if match:
        return match.group(0).strip()
    return username


def _build_calendar_display_label(alias: str | None, calendar_account: str) -> str:
    cleaned = _to_text(alias)
    if not cleaned:
        return ""
    if normalize_instance_key(cleaned) == normalize_instance_key(calendar_account):
        return ""
    return cleaned


def _get_caldav_client_sync(user, tool_id: int):
    credential = ToolCredential.objects.get(user=user, tool_id=tool_id)
    caldav_url = _to_text(credential.config.get("caldav_url"))
    username = _to_text(credential.config.get("username"))
    password = _to_text(credential.config.get("password"))

    if not all([caldav_url, username, password]):
        raise ValueError(_("Incomplete CalDav configuration: missing URL, username, or password"))

    client = caldav.DAVClient(
        url=caldav_url,
        username=username,
        password=password,
    )
    try:
        client.principal()
    except caldav.lib.error.AuthorizationError as exc:
        raise ValueError(f"CalDav authorization failed: {str(exc)}") from exc
    except Exception as exc:
        raise ConnectionError(f"CalDav connection failed: {str(exc)}") from exc
    return client


async def get_caldav_client(user, tool_id: int):
    try:
        return await sync_to_async(_get_caldav_client_sync, thread_sensitive=False)(user, tool_id)
    except ToolCredential.DoesNotExist as exc:
        raise ValueError(_("No CalDav credential found for tool {tool_id}").format(tool_id=tool_id)) from exc
    except Exception:
        logger.exception("CalDav client error for tool_id=%s", tool_id)
        raise


def _list_calendars_sync(user, tool_id: int) -> list[Any]:
    client = _get_caldav_client_sync(user, tool_id)
    principal = client.principal()
    return list(principal.calendars())


async def list_calendars(user, tool_id: int) -> list[str]:
    calendars = await sync_to_async(_list_calendars_sync, thread_sensitive=False)(user, tool_id)
    return [str(getattr(cal, "name", "") or "").strip() for cal in calendars]


async def _resolve_user_for_tool(tool: Tool, agent=None):
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


async def build_calendar_registry(tools: list[Tool], agent=None) -> tuple[Any, list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    if not tools:
        raise ValueError("No CalDAV tools provided.")

    user = await _resolve_user_for_tool(tools[0], agent)
    if not user:
        raise ValueError("Cannot resolve user for CalDAV tools.")

    raw_aliases = [((tool.name or "").strip() or "CalDav") for tool in tools]
    deduped_aliases = dedupe_instance_labels(raw_aliases, default_label="CalDav")

    entries: list[dict[str, Any]] = []
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

        account = _select_calendar_account(config)
        if not account:
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
                "account": account,
                "display_label": _build_calendar_display_label(alias, account),
            }
        )

    if not entries:
        raise ValueError("No configured CalDAV account available.")

    lookup: dict[str, list[dict[str, Any]]] = {}
    selector_values: list[str] = []
    for entry in entries:
        selector = _to_text(entry.get("account"))
        if selector and selector not in selector_values:
            selector_values.append(selector)
        key = normalize_instance_key(selector)
        if key:
            lookup.setdefault(key, []).append(entry)
    return user, entries, lookup, selector_values


def resolve_calendar_account(
    account: str,
    lookup: dict[str, list[dict[str, Any]]],
    selector_values: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    normalized = normalize_instance_key(account)
    matches = lookup.get(normalized, [])
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        requested = _to_text(account) or "<empty>"
        return None, (
            f"Ambiguous calendar_account '{requested}'. Multiple CalDAV tools share this identifier. "
            "Use a unique calendar_account value."
        )
    return None, format_invalid_instance_message(
        selector_name="calendar_account",
        value=account,
        available_labels=selector_values,
    )


def _iter_vevent_components(resource: Any) -> list[Any]:
    ical_instance = getattr(resource, "icalendar_instance", None)
    if ical_instance is None:
        return []
    return [component for component in ical_instance.walk() if getattr(component, "name", None) == "VEVENT"]


def _is_recurring_component(component: Any) -> bool:
    return any(component.get(key) is not None for key in ("RRULE", "RDATE", "EXDATE", "RECURRENCE-ID"))


def _normalize_component(component: Any, *, calendar_name: str) -> dict[str, Any]:
    dtstart = getattr(component.get("DTSTART"), "dt", None)
    dtend = getattr(component.get("DTEND"), "dt", None)
    all_day = isinstance(dtstart, date) and not isinstance(dtstart, datetime)
    return {
        "uid": _to_text(component.get("UID")),
        "calendar_name": _to_text(calendar_name),
        "summary": _to_text(component.get("SUMMARY")),
        "start": _serialize_temporal(dtstart),
        "end": _serialize_temporal(dtend),
        "all_day": bool(all_day),
        "location": _to_text(component.get("LOCATION")),
        "description": _to_text(component.get("DESCRIPTION")),
        "is_recurring": _is_recurring_component(component),
    }


def _normalize_resource(resource: Any, *, calendar_name: str) -> list[dict[str, Any]]:
    return [
        _normalize_component(component, calendar_name=calendar_name)
        for component in _iter_vevent_components(resource)
    ]


def _get_calendar_by_name(calendars: list[Any], calendar_name: str | None) -> list[Any]:
    if not calendar_name:
        return calendars
    selected = [calendar for calendar in calendars if _to_text(getattr(calendar, "name", "")) == _to_text(calendar_name)]
    if not selected:
        raise ValueError(f"Calendar '{calendar_name}' not found.")
    return selected


def _list_events_sync(
    user,
    tool_id: int,
    *,
    start_value: Any,
    end_value: Any,
    calendar_name: str | None = None,
    expand: bool = True,
) -> list[dict[str, Any]]:
    client = _get_caldav_client_sync(user, tool_id)
    principal = client.principal()
    calendars = _get_calendar_by_name(list(principal.calendars()), calendar_name)
    if not calendars:
        return []

    results: list[dict[str, Any]] = []
    for calendar in calendars:
        resources = calendar.search(start=start_value, end=end_value, event=True, expand=expand)
        for resource in resources or []:
            results.extend(_normalize_resource(resource, calendar_name=_to_text(getattr(calendar, "name", ""))))
    results.sort(key=lambda item: (item.get("start") or "", item.get("calendar_name") or "", item.get("summary") or ""))
    return results


async def list_events(
    user,
    tool_id: int,
    start_date: str | datetime,
    end_date: str | datetime,
    calendar_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    start_value = start_date if isinstance(start_date, datetime) else _parse_range_boundary_input(str(start_date), is_end=False)
    end_value = end_date if isinstance(end_date, datetime) else _parse_range_boundary_input(str(end_date), is_end=True)
    return await sync_to_async(_list_events_sync, thread_sensitive=False)(
        user,
        tool_id,
        start_value=start_value,
        end_value=end_value,
        calendar_name=calendar_name,
        expand=True,
    )


async def list_events_to_come(
    user,
    tool_id: int,
    days_ahead: int = 7,
    calendar_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    now = timezone.now()
    end = now + timedelta(days=int(days_ahead or 7))
    return await list_events(
        user,
        tool_id,
        start_date=now,
        end_date=end,
        calendar_name=calendar_name,
    )


def _locate_event_sync(
    user,
    tool_id: int,
    *,
    event_id: str,
    calendar_name: str | None = None,
) -> tuple[Any, Any, dict[str, Any], Any]:
    client = _get_caldav_client_sync(user, tool_id)
    principal = client.principal()
    calendars = _get_calendar_by_name(list(principal.calendars()), calendar_name)

    matches: list[tuple[Any, Any, dict[str, Any], Any]] = []
    for calendar in calendars:
        resources = calendar.search(uid=event_id, event=True, expand=False)
        for resource in resources or []:
            components = _iter_vevent_components(resource)
            if not components:
                continue
            payload = _normalize_component(
                components[0],
                calendar_name=_to_text(getattr(calendar, "name", "")),
            )
            matches.append((calendar, resource, payload, components[0]))

    if not matches:
        raise ValueError(f"Event '{event_id}' not found.")
    if len(matches) > 1 and not calendar_name:
        raise ValueError(
            f"Event '{event_id}' is ambiguous across calendars. Pass --calendar <name>."
        )
    return matches[0]


async def get_event_detail(
    user,
    tool_id: int,
    event_id: str,
    calendar_name: Optional[str] = None,
) -> dict[str, Any]:
    _calendar, _resource, payload, _component = await sync_to_async(
        _locate_event_sync,
        thread_sensitive=False,
    )(user, tool_id, event_id=event_id, calendar_name=calendar_name)
    return payload


async def search_events(
    user,
    tool_id: int,
    query: str,
    days_range: int = 30,
    calendar_name: Optional[str] = None,
) -> list[dict[str, Any]]:
    lowered = _to_text(query).lower()
    now = timezone.now()
    results = await list_events(
        user,
        tool_id,
        start_date=now - timedelta(days=int(days_range or 30)),
        end_date=now + timedelta(days=int(days_range or 30)),
        calendar_name=calendar_name,
    )
    if not lowered:
        return results
    filtered: list[dict[str, Any]] = []
    for item in results:
        haystack = " ".join(
            [
                _to_text(item.get("summary")),
                _to_text(item.get("description")),
                _to_text(item.get("location")),
                _to_text(item.get("calendar_name")),
            ]
        ).lower()
        if lowered in haystack:
            filtered.append(item)
    return filtered


def _create_ical_event(
    *,
    uid: str,
    summary: str,
    start_value: date | datetime,
    end_value: date | datetime | None = None,
    location: str | None = None,
    description: str | None = None,
) -> str:
    event = ICalEvent()
    event.add("uid", uid)
    event.add("summary", summary)
    event.add("dtstart", start_value)
    if end_value is not None:
        event.add("dtend", end_value)
    if location is not None:
        event.add("location", location)
    if description is not None:
        event.add("description", description)

    calendar = ICalendar()
    calendar.add("prodid", "-//Nova//React Terminal V2//EN")
    calendar.add("version", "2.0")
    calendar.add_component(event)
    return calendar.to_ical().decode("utf-8")


def _create_event_sync(
    user,
    tool_id: int,
    *,
    calendar_name: str,
    summary: str,
    start: str,
    end: str | None = None,
    all_day: bool = False,
    location: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    client = _get_caldav_client_sync(user, tool_id)
    principal = client.principal()
    calendar = _get_calendar_by_name(list(principal.calendars()), calendar_name)[0]
    start_value = _parse_temporal_input(start, all_day=all_day)
    end_value = _parse_temporal_input(end, all_day=all_day) if end else None
    ical = _create_ical_event(
        uid=str(uuid.uuid4()),
        summary=summary,
        start_value=start_value,
        end_value=end_value,
        location=location,
        description=description,
    )
    resource = calendar.add_event(ical)
    normalized = _normalize_resource(resource, calendar_name=_to_text(getattr(calendar, "name", "")))
    if not normalized:
        raise ValueError("Created event could not be normalized.")
    return normalized[0]


async def create_event(
    user,
    tool_id: int,
    *,
    calendar_name: str,
    summary: str,
    start: str,
    end: str | None = None,
    all_day: bool = False,
    location: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return await sync_to_async(_create_event_sync, thread_sensitive=False)(
        user,
        tool_id,
        calendar_name=calendar_name,
        summary=summary,
        start=start,
        end=end,
        all_day=all_day,
        location=location,
        description=description,
    )


def _update_event_sync(
    user,
    tool_id: int,
    *,
    event_id: str,
    calendar_name: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    all_day: bool | None = None,
    location: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    calendar, resource, payload, component = _locate_event_sync(
        user,
        tool_id,
        event_id=event_id,
        calendar_name=calendar_name,
    )
    if payload.get("is_recurring"):
        raise ValueError("Recurring events are read-only in React Terminal v2.")

    effective_all_day = payload.get("all_day") if all_day is None else bool(all_day)
    if summary is not None:
        component["SUMMARY"] = summary
    if start is not None:
        component["DTSTART"] = _parse_temporal_input(start, all_day=effective_all_day)
    if end is not None:
        component["DTEND"] = _parse_temporal_input(end, all_day=effective_all_day)
    if location is not None:
        if location:
            component["LOCATION"] = location
        elif component.get("LOCATION") is not None:
            del component["LOCATION"]
    if description is not None:
        if description:
            component["DESCRIPTION"] = description
        elif component.get("DESCRIPTION") is not None:
            del component["DESCRIPTION"]

    resource.icalendar_instance = resource.icalendar_instance
    resource.save()
    normalized = _normalize_resource(resource, calendar_name=_to_text(getattr(calendar, "name", "")))
    if not normalized:
        raise ValueError("Updated event could not be normalized.")
    return normalized[0]


async def update_event(
    user,
    tool_id: int,
    *,
    event_id: str,
    calendar_name: str,
    summary: str | None = None,
    start: str | None = None,
    end: str | None = None,
    all_day: bool | None = None,
    location: str | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    return await sync_to_async(_update_event_sync, thread_sensitive=False)(
        user,
        tool_id,
        event_id=event_id,
        calendar_name=calendar_name,
        summary=summary,
        start=start,
        end=end,
        all_day=all_day,
        location=location,
        description=description,
    )


def _delete_event_sync(
    user,
    tool_id: int,
    *,
    event_id: str,
    calendar_name: str | None = None,
) -> dict[str, Any]:
    _calendar, resource, payload, _component = _locate_event_sync(
        user,
        tool_id,
        event_id=event_id,
        calendar_name=calendar_name,
    )
    if payload.get("is_recurring"):
        raise ValueError("Recurring events are read-only in React Terminal v2.")
    resource.delete()
    return payload


async def delete_event(
    user,
    tool_id: int,
    *,
    event_id: str,
    calendar_name: str | None = None,
) -> dict[str, Any]:
    return await sync_to_async(_delete_event_sync, thread_sensitive=False)(
        user,
        tool_id,
        event_id=event_id,
        calendar_name=calendar_name,
    )
