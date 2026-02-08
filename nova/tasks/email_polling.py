from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from django.utils import timezone

from nova.models.TaskDefinition import TaskDefinition
from nova.models.Tool import ToolCredential
from nova.tools.builtins.email import build_imap_client, decode_str, safe_get, safe_imap_logout

logger = logging.getLogger(__name__)


def _status_get(status: dict[Any, Any], key: str, default=None):
    return status.get(key) or status.get(key.encode("utf-8")) or default


def _address_to_text(address_obj) -> str:
    if not address_obj:
        return ""
    name = decode_str(getattr(address_obj, "name", "") or "")
    mailbox = decode_str(getattr(address_obj, "mailbox", "") or "")
    host = decode_str(getattr(address_obj, "host", "") or "")
    addr = f"{mailbox}@{host}" if mailbox and host else mailbox or host
    if name and addr:
        return f"{name} <{addr}>"
    return addr or name or ""


def _sender_from_envelope(envelope) -> str:
    if not envelope:
        return ""
    sender = getattr(envelope, "sender", None) or getattr(envelope, "from_", None)
    if not sender:
        return ""
    try:
        first = sender[0] if isinstance(sender, (list, tuple)) else sender
    except Exception:
        first = sender
    return _address_to_text(first)


def _iso(value) -> str:
    if not value:
        return ""
    if isinstance(value, dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.isoformat()
    return str(value)


def poll_new_unseen_email_headers(task_definition: TaskDefinition):
    """Poll IMAP INBOX for unseen messages and return only *new* headers.

    Important policy:
    - read-only polling (never modify seen/unseen flags)
    - first run processes existing unseen emails
    - dedup by UID / UIDVALIDITY cursor
    - ignore backlog after long downtime (> 2x polling interval)
    """

    if task_definition.trigger_type != TaskDefinition.TriggerType.EMAIL_POLL:
        raise ValueError("poll_new_unseen_email_headers requires an email polling task definition")
    if not task_definition.email_tool_id:
        raise ValueError("Email tool is required for email polling")

    cred = ToolCredential.objects.filter(user=task_definition.user, tool_id=task_definition.email_tool_id).first()
    if not cred:
        raise ValueError("No credential found for selected email tool.")

    state = dict(task_definition.runtime_state or {})
    now = timezone.now()
    interval = int(task_definition.poll_interval_minutes or 5)

    last_poll_iso = state.get("last_poll_at")
    last_poll_at = None
    if last_poll_iso:
        try:
            last_poll_at = dt.datetime.fromisoformat(last_poll_iso)
            if last_poll_at.tzinfo is None:
                last_poll_at = last_poll_at.replace(tzinfo=dt.timezone.utc)
        except Exception:
            last_poll_at = None

    client = None
    try:
        client = build_imap_client(cred)
        status = client.select_folder("INBOX")
        uidvalidity = int(_status_get(status, "UIDVALIDITY", 0) or 0)

        unseen_uids = sorted(client.search(["UNSEEN"]))
        last_uid = int(state.get("last_uid", 0) or 0)
        old_uidvalidity = int(state.get("uidvalidity", 0) or 0)

        # If mailbox UIDVALIDITY changed, reset cursor safely.
        if old_uidvalidity and uidvalidity and old_uidvalidity != uidvalidity:
            last_uid = 0

        # Ignore backlog after downtime > 2x interval.
        if last_poll_at and (now - last_poll_at) > dt.timedelta(minutes=interval * 2):
            next_state = {
                **state,
                "initialized": True,
                "uidvalidity": uidvalidity,
                "last_uid": max(unseen_uids) if unseen_uids else last_uid,
                "last_poll_at": now.isoformat(),
                "backlog_skipped_at": now.isoformat(),
            }
            return {"headers": [], "state": next_state, "skip_reason": "backlog_skipped"}

        # First run: process existing unseen by design (last_uid defaults to 0).
        new_uids = [uid for uid in unseen_uids if int(uid) > last_uid]
        headers = []
        if new_uids:
            fetch_data = client.fetch(new_uids, ["ENVELOPE"])
            for uid in new_uids:
                msg = fetch_data.get(uid, {})
                envelope = safe_get(msg, "ENVELOPE")
                headers.append(
                    {
                        "uid": int(uid),
                        "from": _sender_from_envelope(envelope),
                        "subject": decode_str(getattr(envelope, "subject", "") if envelope else ""),
                        "date": _iso(getattr(envelope, "date", None) if envelope else None),
                    }
                )

        next_state = {
            **state,
            "initialized": True,
            "uidvalidity": uidvalidity,
            "last_uid": max(new_uids) if new_uids else last_uid,
            "last_poll_at": now.isoformat(),
        }
        return {"headers": headers, "state": next_state, "skip_reason": None}
    finally:
        safe_imap_logout(client)
