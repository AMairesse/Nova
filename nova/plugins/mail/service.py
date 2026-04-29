# nova/plugins/mail/service.py
from __future__ import annotations

import imapclient
import logging
import os
import re
import smtplib
from email import encoders as email_encoders
from email.header import decode_header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any
from urllib.parse import urlparse

from asgiref.sync import sync_to_async
from django.utils.translation import gettext_lazy as _

from nova.models.Tool import Tool, ToolCredential
from nova.plugins.shared.multi_instance import (
    build_selector_schema,
    dedupe_instance_labels,
    format_invalid_instance_message,
    normalize_instance_key,
)
from nova.web.network_policy import assert_allowed_egress_host_port_sync

logger = logging.getLogger(__name__)


EMAIL_CLIENT_TIMEOUT = int(os.getenv("NOVA_EMAIL_CLIENT_TIMEOUT", "30"))
EMAIL_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LOCAL_PART_RE = re.compile(r"^[A-Za-z0-9._%+\-]+$")
KNOWN_MAIL_HOST_PREFIXES = {"imap", "pop", "pop3", "smtp", "mail", "mx"}
MAILBOX_SPECIAL_USE_FLAGS = {
    "inbox": "inbox",
    "junk": "junk",
    "spam": "junk",
    "trash": "trash",
    "archive": "archive",
    "sent": "sent",
    "drafts": "drafts",
}
MAILBOX_SPECIAL_NAME_FALLBACKS = {
    "inbox": {"inbox"},
    "junk": {"junk", "spam", "junkemail", "bulkmail", "gmailspam"},
    "trash": {"trash", "deleteditems", "deletedmessages", "bin", "gmailtrash"},
    "archive": {"archive", "allmail", "gmailallmail"},
    "sent": {"sent", "sentitems", "sentmail", "gmailsentmail"},
    "drafts": {"drafts", "draft", "draftmessages", "gmaildrafts"},
}
MAIL_MARK_ACTIONS = {
    "seen": ("add", "\\Seen", "seen"),
    "unseen": ("remove", "\\Seen", "unseen"),
    "flagged": ("add", "\\Flagged", "flagged"),
    "unflagged": ("remove", "\\Flagged", "unflagged"),
}

def safe_imap_logout(client):
    if client:
        try:
            client.logout()
        except Exception as error:
            logger.warning("IMAP logout failed: %s", error)


def safe_smtp_quit(server):
    if server:
        try:
            server.quit()
        except Exception as error:
            logger.warning("SMTP quit failed: %s", error)


def build_imap_client(credential):
    config = credential.config or {}
    imap_server = config.get("imap_server")
    imap_port = config.get("imap_port", 993)
    username = config.get("username")
    password = config.get("password")
    use_ssl = config.get("use_ssl", True)

    if not all([imap_server, username, password]):
        raise ValueError(_("Incomplete IMAP configuration: missing server, username, or password"))

    assert_allowed_egress_host_port_sync(imap_server, int(imap_port or 0))
    client = imapclient.IMAPClient(
        imap_server,
        port=imap_port,
        ssl=use_ssl,
        timeout=EMAIL_CLIENT_TIMEOUT,
    )
    client.login(username, password)
    client._server_capabilities = client.capabilities()
    return client


def build_smtp_client(credential):
    config = credential.config or {}
    smtp_server = config.get("smtp_server")
    smtp_port = config.get("smtp_port", 587)
    smtp_use_tls = config.get("smtp_use_tls", True)
    username = config.get("username")
    password = config.get("password")

    if not all([smtp_server, username, password]):
        raise ValueError(_("Incomplete SMTP configuration: missing server, username, or password"))

    assert_allowed_egress_host_port_sync(smtp_server, int(smtp_port or 0))
    if smtp_use_tls:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=EMAIL_CLIENT_TIMEOUT)
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=EMAIL_CLIENT_TIMEOUT)

    server.login(username, password)
    return server


async def get_imap_client(user, tool_id):
    try:
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
    except ToolCredential.DoesNotExist as exc:
        raise ValueError(_("No IMAP credential found for tool {tool_id}").format(tool_id=tool_id)) from exc
    return build_imap_client(credential)


def decode_str(text):
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="ignore")
    if not text:
        return ""
    try:
        decoded_parts = decode_header(text)
        result = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                result += part.decode(encoding or "utf-8", errors="ignore")
            else:
                result += str(part)
        return result
    except Exception:
        return text


def safe_get(data, key):
    if isinstance(key, str):
        return data.get(key) or data.get(key.encode("utf-8"))
    return data.get(key) or data.get(key.decode("utf-8"))


def _iter_message_leaf_parts(message_obj, prefix: str = ""):
    payload = message_obj.get_payload()
    if isinstance(payload, list):
        for index, part in enumerate(payload, start=1):
            child_prefix = f"{prefix}.{index}" if prefix else str(index)
            yield from _iter_message_leaf_parts(part, child_prefix)
        return
    yield prefix or "1", message_obj


def _collect_email_attachments(email_message) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for attachment_id, part in _iter_message_leaf_parts(email_message):
        filename = decode_str(part.get_filename() or "")
        disposition = str(part.get_content_disposition() or "").strip().lower()
        if not filename and disposition not in {"attachment", "inline"}:
            continue

        content = part.get_payload(decode=True) or b""
        attachments.append(
            {
                "attachment_id": attachment_id,
                "filename": filename or f"attachment-{attachment_id}",
                "mime_type": str(part.get_content_type() or "application/octet-stream").strip().lower(),
                "size": len(content),
                "content": content,
            }
        )
    return attachments


def _normalize_mailbox_name_key(name: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(name or "").strip().lower())


def _normalize_flag_name(flag: Any) -> str:
    return decode_str(flag).strip().lstrip("\\").lower()


def _normalize_mail_flags(flags: Any) -> list[str]:
    normalized: list[str] = []
    for flag in list(flags or []):
        text = decode_str(flag).strip()
        if text and text not in normalized:
            normalized.append(text)
    return normalized


def _format_mail_flags(flags: Any) -> str:
    normalized = _normalize_mail_flags(flags)
    return ", ".join(normalized) if normalized else "none"


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


def _first_address_text(addresses) -> str:
    if not addresses:
        return ""
    try:
        first = addresses[0] if isinstance(addresses, (list, tuple)) else addresses
    except Exception:
        first = addresses
    return _address_to_text(first)


def _sender_from_envelope(envelope) -> str:
    if not envelope:
        return ""
    return _first_address_text(
        getattr(envelope, "sender", None) or getattr(envelope, "from_", None)
    )


def _recipient_from_envelope(envelope) -> str:
    if not envelope:
        return ""
    return _first_address_text(getattr(envelope, "to", None))


def _format_envelope_date(envelope) -> str:
    value = getattr(envelope, "date", None) if envelope else None
    if not value:
        return "Unknown"
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "Invalid date"


def _detect_mailbox_special_use(name: str, flags: list[str]) -> tuple[str, str] | tuple[None, None]:
    for flag in flags:
        special_use = MAILBOX_SPECIAL_USE_FLAGS.get(_normalize_flag_name(flag))
        if special_use:
            return special_use, "flag"

    normalized_name = _normalize_mailbox_name_key(name)
    for special_use, aliases in MAILBOX_SPECIAL_NAME_FALLBACKS.items():
        if normalized_name in aliases:
            return special_use, "name"

    return None, None


def _list_mailboxes_with_details(client) -> list[dict[str, Any]]:
    details: list[dict[str, Any]] = []
    for mailbox in client.list_folders():
        if isinstance(mailbox, tuple) and len(mailbox) >= 3:
            raw_flags, delimiter, name = mailbox
        else:
            raw_flags, delimiter, name = (), "", str(mailbox)
        flags = _normalize_mail_flags(raw_flags)
        special_use, special_source = _detect_mailbox_special_use(str(name), flags)
        details.append(
            {
                "name": str(name),
                "delimiter": str(delimiter or ""),
                "flags": flags,
                "special_use": special_use,
                "special_source": special_source,
            }
        )
    return details


def resolve_special_mailbox(client, special_use: str) -> str | None:
    requested = str(special_use or "").strip().lower()
    details = _list_mailboxes_with_details(client)
    for source in ("flag", "name"):
        for mailbox in details:
            if mailbox.get("special_use") == requested and mailbox.get("special_source") == source:
                return str(mailbox.get("name") or "")
    return None


def _resolve_message_uids(
    *,
    message_ids: list[int] | None = None,
    uids: list[int] | None = None,
) -> list[int]:
    resolved: list[int] = []
    for raw in [*(message_ids or []), *(uids or [])]:
        value = int(raw)
        if value not in resolved:
            resolved.append(value)
    if not resolved:
        raise ValueError(_("At least one email id or uid is required."))
    return resolved


def _selector_label(message_id: int | None = None, uid: int | None = None) -> str:
    if uid is not None:
        return _("UID {uid}").format(uid=uid)
    return _("ID {id}").format(id=message_id)


async def _fetch_email_message_data(
    user,
    tool_id,
    message_id: int | None = None,
    *,
    uid: int | None = None,
    folder: str = "INBOX",
):
    target_uid = int(uid if uid is not None else message_id)
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)
        messages = client.fetch([target_uid], ["ENVELOPE", "BODY.PEEK[]", "UID", "FLAGS"])
        return messages.get(target_uid)
    finally:
        safe_imap_logout(client)


async def _load_email_message_with_attachments(
    user,
    tool_id,
    message_id: int | None = None,
    *,
    uid: int | None = None,
    folder: str = "INBOX",
):
    msg_data = await _fetch_email_message_data(user, tool_id, message_id, uid=uid, folder=folder)
    if not msg_data:
        return None, None, None, [], []

    import email

    envelope = safe_get(msg_data, "ENVELOPE")
    body = safe_get(msg_data, "BODY[]")
    resolved_uid = safe_get(msg_data, "UID")
    flags = _normalize_mail_flags(safe_get(msg_data, "FLAGS"))
    if not body:
        return envelope, None, resolved_uid, flags, []

    email_message = email.message_from_bytes(body)
    return envelope, email_message, resolved_uid, flags, _collect_email_attachments(email_message)


def _build_email_attachment_manifest_lines(attachments: list[dict[str, Any]]) -> list[str]:
    lines = []
    for item in attachments:
        lines.append(
            _("- attachment_id=%(attachment_id)s | name=%(filename)s | type=%(mime_type)s | size=%(size)s bytes")
            % {
                "attachment_id": item.get("attachment_id"),
                "filename": item.get("filename"),
                "mime_type": item.get("mime_type") or "application/octet-stream",
                "size": int(item.get("size") or 0),
            }
        )
    return lines


def _attach_binary_parts(msg, attachments: list) -> None:
    for attachment in list(attachments or []):
        maintype, _, subtype = str(attachment.mime_type or "application/octet-stream").partition("/")
        maintype = maintype or "application"
        subtype = subtype or "octet-stream"
        part = MIMEBase(maintype, subtype)
        part.set_payload(attachment.content)
        email_encoders.encode_base64(part)
        part.add_header(
            "Content-Disposition",
            "attachment",
            filename=str(attachment.filename or "attachment"),
        )
        msg.attach(part)


def format_email_info(msg_id, envelope, *, uid: int | None = None, flags: list[str] | None = None):
    if not envelope:
        return f"ID: {msg_id} | UID: {uid if uid is not None else msg_id} | Flags: {_format_mail_flags(flags)} | [No envelope data]"

    subject = "[No subject]"
    if hasattr(envelope, "subject") and envelope.subject:
        subject = decode_str(envelope.subject)

    sender = _sender_from_envelope(envelope) or "[No sender]"
    date = _format_envelope_date(envelope)
    return (
        f"ID: {msg_id} | UID: {uid if uid is not None else msg_id} | Flags: {_format_mail_flags(flags)} "
        f"| From: {sender} | Subject: {subject} | Date: {date}"
    )


async def list_emails(user, tool_id, folder: str = "INBOX", limit: int = 10) -> str:
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)
        messages = client.search(["ALL"])
        if not messages:
            return _("No emails found in {folder}").format(folder=folder)

        recent_messages = sorted(messages, reverse=True)[:limit]
        fetch_data = client.fetch(recent_messages, ["ENVELOPE", "UID", "FLAGS"])

        result = _("Recent emails in {folder}:\n").format(folder=folder)
        for msg_id in recent_messages:
            msg_data = fetch_data.get(msg_id, {})
            envelope = safe_get(msg_data, "ENVELOPE")
            result += format_email_info(
                msg_id,
                envelope,
                uid=safe_get(msg_data, "UID"),
                flags=_normalize_mail_flags(safe_get(msg_data, "FLAGS")),
            ) + "\n"
        return result
    finally:
        safe_imap_logout(client)


def _extract_email_text(email_message) -> str:
    if not email_message:
        return ""
    if email_message.is_multipart():
        for part in email_message.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True) or b""
                return payload.decode(charset, errors="ignore")
        return ""
    charset = email_message.get_content_charset() or "utf-8"
    payload = email_message.get_payload(decode=True) or b""
    return payload.decode(charset, errors="ignore")


async def read_email(
    user,
    tool_id,
    message_id: int | None = None,
    *,
    uid: int | None = None,
    folder: str = "INBOX",
    preview_only: bool = True,
) -> str:
    envelope, email_message, resolved_uid, flags, attachments = await _load_email_message_with_attachments(
        user,
        tool_id,
        message_id,
        uid=uid,
        folder=folder,
    )
    if envelope is None and email_message is None:
        return _("Email with {selector} not found.").format(
            selector=_selector_label(message_id=message_id, uid=uid)
        )
    if email_message is None:
        return _("Email body not available.")

    result = _("Email Details:\n")
    result += _("ID: {id}\n").format(id=message_id if message_id is not None else resolved_uid or uid or "")
    result += _("UID: {uid}\n").format(uid=resolved_uid or uid or message_id or "")
    result += _("Flags: {flags}\n").format(flags=_format_mail_flags(flags))
    if envelope:
        sender = _sender_from_envelope(envelope) or "[Not available]"
        to_addr = _recipient_from_envelope(envelope) or "[Not available]"
        subject = decode_str(getattr(envelope, "subject", None))
        date = _format_envelope_date(envelope)
        result += _("From: {sender}\n").format(sender=sender)
        result += _("To: {to}\n").format(to=to_addr)
        result += _("Subject: {subject}\n").format(subject=subject)
        result += _("Date: {date}\n").format(date=date)
    else:
        result += _("From: [Not available]\nTo: [Not available]\nSubject: [Not available]\nDate: [Not available]\n")

    if attachments:
        result += "\n" + _("Attachments:\n")
        result += "\n".join(_build_email_attachment_manifest_lines(attachments))
    else:
        result += "\n" + _("Attachments: none")

    if preview_only:
        result += "\n\n" + _("Content Preview (first 500 characters):\n")
        content = _extract_email_text(email_message)

        if len(content) > 500:
            result += content[:500] + "..."
            result += _("\n\n[Content truncated. Use preview_only=False to read full email]")
        else:
            result += content
        return result

    result += "\n\n" + _("Full Content:\n")
    result += _extract_email_text(email_message)
    return result


async def list_email_attachments(
    user,
    tool_id,
    message_id: int | None = None,
    *,
    uid: int | None = None,
    folder: str = "INBOX",
) -> str:
    envelope, email_message, resolved_uid, flags, attachments = await _load_email_message_with_attachments(
        user,
        tool_id,
        message_id,
        uid=uid,
        folder=folder,
    )
    if envelope is None and email_message is None:
        return _("Email with {selector} not found.").format(
            selector=_selector_label(message_id=message_id, uid=uid)
        )
    if not attachments:
        return _("No attachments found for email {selector}.").format(
            selector=_selector_label(message_id=message_id, uid=uid)
        )

    subject = decode_str(getattr(envelope, "subject", "") if envelope else "")
    lines = [
        _("Attachments for email {id} (uid={uid}, flags={flags}, subject={subject}):").format(
            id=message_id if message_id is not None else resolved_uid or uid or "",
            uid=resolved_uid or uid or message_id or "",
            flags=_format_mail_flags(flags),
            subject=subject or _("no subject"),
        )
    ]
    lines.extend(_build_email_attachment_manifest_lines(attachments))
    return "\n".join(lines)


async def move_emails(
    user,
    tool_id,
    *,
    message_ids: list[int] | None = None,
    uids: list[int] | None = None,
    source_folder: str = "INBOX",
    target_folder: str | None = None,
    target_special: str | None = None,
) -> str:
    requested_special = str(target_special or "").strip().lower()
    if bool(target_folder) == bool(requested_special):
        raise ValueError(_("Choose exactly one destination: --to-folder or --to-special."))
    if requested_special and requested_special not in {"junk", "trash", "archive"}:
        raise ValueError(_("Unsupported special mailbox: {special}").format(special=requested_special))

    target_uids = _resolve_message_uids(message_ids=message_ids, uids=uids)
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(source_folder)
        resolved_target_folder = str(target_folder or "").strip()
        if requested_special:
            resolved_target_folder = str(resolve_special_mailbox(client, requested_special) or "").strip()
            if not resolved_target_folder:
                raise ValueError(
                    _("No %(special)s mailbox could be resolved for this account.") % {"special": requested_special}
                )

        if _normalize_mailbox_name_key(resolved_target_folder) == _normalize_mailbox_name_key(source_folder):
            return _("Emails are already in {folder}.").format(folder=resolved_target_folder)

        if client.has_capability("MOVE"):
            client.move(target_uids, resolved_target_folder)
        else:
            client.copy(target_uids, resolved_target_folder)
            client.delete_messages(target_uids)
            client.expunge(target_uids)

        return _("Moved {count} email(s) from {source} to {dest}.").format(
            count=len(target_uids),
            source=source_folder,
            dest=resolved_target_folder,
        )
    finally:
        safe_imap_logout(client)


async def mark_emails(
    user,
    tool_id,
    *,
    message_ids: list[int] | None = None,
    uids: list[int] | None = None,
    folder: str = "INBOX",
    action: str,
) -> str:
    normalized_action = str(action or "").strip().lower()
    if normalized_action not in MAIL_MARK_ACTIONS:
        raise ValueError(_("Unsupported mark action: {action}").format(action=action))

    target_uids = _resolve_message_uids(message_ids=message_ids, uids=uids)
    operation, flag, label = MAIL_MARK_ACTIONS[normalized_action]
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)
        if operation == "add":
            client.add_flags(target_uids, [flag])
        else:
            client.remove_flags(target_uids, [flag])
        return _("Marked {count} email(s) in {folder} as {label}.").format(
            count=len(target_uids),
            folder=folder,
            label=label,
        )
    finally:
        safe_imap_logout(client)


async def list_mailboxes(user, tool_id) -> str:
    client = await get_imap_client(user, tool_id)
    try:
        mailboxes = _list_mailboxes_with_details(client)
        result = _("Available mailboxes:\n")
        for mailbox in mailboxes:
            name = str(mailbox.get("name") or "")
            special_use = str(mailbox.get("special_use") or "").strip()
            flags = _format_mail_flags(mailbox.get("flags"))
            extras: list[str] = []
            if special_use:
                extras.append(f"special: {special_use}")
            if flags != "none":
                extras.append(f"flags: {flags}")
            if extras:
                result += f"- {name} [{'; '.join(extras)}]\n"
            else:
                result += f"- {name}\n"
        return result
    finally:
        safe_imap_logout(client)


async def test_email_access(user, tool_id):
    try:
        client = await get_imap_client(user, tool_id)
        try:
            client.list_folders()
            credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
            config = credential.config or {}
            if config.get("enable_sending") and config.get("smtp_server"):
                server = None
                try:
                    server = build_smtp_client(credential)
                finally:
                    safe_smtp_quit(server)
            return {"status": "success", "message": _("IMAP connection successful")}
        finally:
            safe_imap_logout(client)
    except ToolCredential.DoesNotExist:
        return {"status": "error", "message": _("No email credential found for tool {tool_id}").format(tool_id=tool_id)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


async def _get_credential(user, tool_id: int) -> ToolCredential | None:
    try:
        return await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
    except ToolCredential.DoesNotExist:
        return None


def _mailbox_account(credential: ToolCredential | None) -> str:
    if not credential:
        return ""
    cfg = credential.config or {}
    return (cfg.get("from_address") or cfg.get("username") or "").strip()


def _extract_email_address(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    match = EMAIL_ADDRESS_RE.search(text)
    return match.group(0).strip() if match else ""


def _extract_host(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urlparse(raw)
        return str(parsed.hostname or "").strip().lower()
    return raw.split("/")[0].split(":")[0].strip().lower()


def _derive_domain_from_imap_server(imap_server: str | None) -> str:
    host = _extract_host(imap_server)
    if not host or "." not in host:
        return ""

    parts = [part for part in host.split(".") if part]
    while len(parts) > 2 and parts and parts[0] in KNOWN_MAIL_HOST_PREFIXES:
        parts = parts[1:]
    return ".".join(parts)


def _clean_mailbox_alias_label(alias: str | None) -> str:
    text = str(alias or "").strip()
    if not text:
        return ""
    return re.sub(r"^\s*Email\s*-\s*", "", text, flags=re.IGNORECASE).strip()


def _select_mailbox_email(config: dict, credential: ToolCredential | None) -> str:
    username = str(config.get("username") or "").strip()
    from_address = str(config.get("from_address") or "").strip()
    account = _mailbox_account(credential)

    for candidate in (username, from_address, account):
        text = _extract_email_address(candidate)
        if text:
            return text

    if username and LOCAL_PART_RE.fullmatch(username):
        domain = _derive_domain_from_imap_server(config.get("imap_server"))
        if domain:
            inferred_email = _extract_email_address(f"{username}@{domain}")
            if inferred_email:
                return inferred_email

    return username or from_address or account


def _build_mailbox_display_label(alias: str | None, selector_email: str) -> str:
    cleaned = _clean_mailbox_alias_label(alias)
    if not cleaned:
        return ""
    if normalize_instance_key(cleaned) == normalize_instance_key(selector_email):
        return ""
    return cleaned


def _mailbox_lookup_keys(entry: dict) -> list[str]:
    raw_keys = [
        entry.get("selector_email"),
        entry.get("account"),
        entry.get("username"),
        entry.get("from_address"),
    ]
    out: list[str] = []
    for raw in raw_keys:
        key = normalize_instance_key(raw)
        if key and key not in out:
            out.append(key)
    return out


async def _build_mailbox_registry(tools: list[Tool], agent: Any) -> tuple:
    if not tools:
        raise ValueError("No email tools provided for aggregation.")

    user = getattr(agent, "user", None) or getattr(tools[0], "user", None)
    if not user:
        raise ValueError("Cannot resolve user for aggregated email tool.")

    raw_aliases = [((tool.name or "").strip() or "Email") for tool in tools]
    deduped_aliases = dedupe_instance_labels(raw_aliases, default_label="Email")

    entries = []
    for tool, base_alias, alias in zip(tools, raw_aliases, deduped_aliases):
        tool_id = getattr(tool, "id", None)
        if not tool_id:
            continue

        if alias != base_alias:
            logger.warning(
                "Duplicate or empty email alias '%s' detected for tool_id=%s; using '%s'.",
                base_alias,
                tool_id,
                alias,
            )

        credential = await _get_credential(user, tool_id)
        if not credential:
            logger.warning(
                "Skipping email alias '%s' (tool_id=%s): no credential configured for user_id=%s.",
                alias,
                tool_id,
                getattr(user, "id", "unknown"),
            )
            continue

        config = credential.config or {}
        if not all([config.get("imap_server"), config.get("username"), config.get("password")]):
            logger.warning(
                "Skipping email alias '%s' (tool_id=%s): incomplete IMAP configuration.",
                alias,
                tool_id,
            )
            continue

        entries.append(
            {
                "alias": alias,
                "tool_id": tool_id,
                "credential": credential,
                "account": _mailbox_account(credential),
                "selector_email": _select_mailbox_email(config, credential),
                "username": (config.get("username") or "").strip(),
                "from_address": (config.get("from_address") or "").strip(),
                "can_send": bool(config.get("enable_sending", False)),
            }
        )

    for entry in entries:
        entry["display_label"] = _build_mailbox_display_label(
            entry.get("alias"),
            str(entry.get("selector_email") or ""),
        )

    if not entries:
        raise ValueError("No configured email mailbox available for aggregation.")

    selector_values: list[str] = []
    for entry in entries:
        selector = str(entry.get("selector_email") or "").strip()
        if selector and selector not in selector_values:
            selector_values.append(selector)

    lookup: dict[str, list[dict]] = {}
    for entry in entries:
        for key in _mailbox_lookup_keys(entry):
            lookup.setdefault(key, []).append(entry)

    mailbox_schema = build_selector_schema(
        selector_name="mailbox",
        labels=selector_values,
        description=f"Mailbox email address to use. Available addresses: {', '.join(selector_values)}.",
    )
    return user, entries, lookup, mailbox_schema, selector_values


def _resolve_mailbox(mailbox: str, lookup: dict[str, list[dict]], selector_values: list[str]) -> tuple:
    matches = lookup.get(normalize_instance_key(mailbox), [])
    if len(matches) == 1:
        return matches[0], None

    if len(matches) > 1:
        resolved_aliases: list[str] = []
        for item in matches:
            address = str(item.get("selector_email") or "").strip()
            if address and address not in resolved_aliases:
                resolved_aliases.append(address)
        return None, (
            f"Ambiguous mailbox '{str(mailbox or '').strip() or '<empty>'}'. "
            f"Use one of these email addresses: {', '.join(resolved_aliases)}."
        )

    return None, format_invalid_instance_message(
        selector_name="mailbox",
        value=mailbox,
        available_labels=selector_values,
    )
