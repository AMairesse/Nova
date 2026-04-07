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
from typing import Any, Optional
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

logger = logging.getLogger(__name__)


EMAIL_CLIENT_TIMEOUT = int(os.getenv("NOVA_EMAIL_CLIENT_TIMEOUT", "30"))
EMAIL_ADDRESS_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
LOCAL_PART_RE = re.compile(r"^[A-Za-z0-9._%+\-]+$")
KNOWN_MAIL_HOST_PREFIXES = {"imap", "pop", "pop3", "smtp", "mail", "mx"}

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


async def _fetch_email_message_data(user, tool_id, message_id: int, *, folder: str = "INBOX"):
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)
        messages = client.fetch([message_id], ["ENVELOPE", "BODY.PEEK[]", "UID"])
        if message_id not in messages:
            return None
        return messages[message_id]
    finally:
        safe_imap_logout(client)


async def _load_email_message_with_attachments(user, tool_id, message_id: int, *, folder: str = "INBOX"):
    msg_data = await _fetch_email_message_data(user, tool_id, message_id, folder=folder)
    if not msg_data:
        return None, None, None, []

    import email

    envelope = safe_get(msg_data, "ENVELOPE")
    body = safe_get(msg_data, "BODY[]")
    uid = safe_get(msg_data, "UID")
    if not body:
        return envelope, None, uid, []

    email_message = email.message_from_bytes(body)
    return envelope, email_message, uid, _collect_email_attachments(email_message)


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


def format_email_info(msg_id, envelope):
    if not envelope:
        return f"ID: {msg_id} | [No envelope data]"

    subject = "[No subject]"
    if hasattr(envelope, "subject") and envelope.subject:
        subject = decode_str(envelope.subject)

    sender = "[No sender]"
    if hasattr(envelope, "sender") and envelope.sender:
        sender = decode_str(envelope.sender)

    date = "Unknown"
    if hasattr(envelope, "date") and envelope.date:
        try:
            date = envelope.date.strftime("%Y-%m-%d %H:%M")
        except Exception:
            date = "Invalid date"

    return f"ID: {msg_id} | From: {sender} | Subject: {subject} | Date: {date}"


async def list_emails(user, tool_id, folder: str = "INBOX", limit: int = 10) -> str:
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)
        messages = client.search(["ALL"])
        if not messages:
            return _("No emails found in {folder}").format(folder=folder)

        recent_messages = sorted(messages, reverse=True)[:limit]
        fetch_data = client.fetch(recent_messages, ["ENVELOPE"])

        result = _("Recent emails in {folder}:\n").format(folder=folder)
        for msg_id in recent_messages:
            msg_data = fetch_data.get(msg_id, {})
            envelope = safe_get(msg_data, "ENVELOPE")
            result += format_email_info(msg_id, envelope) + "\n"
        return result
    finally:
        safe_imap_logout(client)


async def read_email(user, tool_id, message_id: int, folder: str = "INBOX", preview_only: bool = True) -> str:
    envelope, email_message, _uid, attachments = await _load_email_message_with_attachments(
        user,
        tool_id,
        message_id,
        folder=folder,
    )
    if envelope is None and email_message is None:
        return _("Email with ID {id} not found.").format(id=message_id)
    if email_message is None:
        return _("Email body not available.")

    result = _("Email Details:\n")
    if envelope:
        sender = decode_str(getattr(envelope, "sender", None))
        to_addr = decode_str(getattr(envelope, "to", [None])[0] if getattr(envelope, "to", None) else None)
        subject = decode_str(getattr(envelope, "subject", None))
        date = envelope.date.strftime("%Y-%m-%d %H:%M") if getattr(envelope, "date", None) else "Unknown"
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
        content = ""
        if email_message.is_multipart():
            for part in email_message.walk():
                if part.get_content_type() == "text/plain":
                    charset = part.get_content_charset() or "utf-8"
                    content = part.get_payload(decode=True).decode(charset, errors="ignore")
                    break
        else:
            charset = email_message.get_content_charset() or "utf-8"
            content = email_message.get_payload(decode=True).decode(charset, errors="ignore")

        if len(content) > 500:
            result += content[:500] + "..."
            result += _("\n\n[Content truncated. Use preview_only=False to read full email]")
        else:
            result += content
        return result

    result += "\n\n" + _("Full Content:\n")
    if email_message.is_multipart():
        for part in email_message.walk():
            if part.get_content_type() == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                result += part.get_payload(decode=True).decode(charset, errors="ignore")
                break
    else:
        charset = email_message.get_content_charset() or "utf-8"
        result += email_message.get_payload(decode=True).decode(charset, errors="ignore")
    return result


async def list_email_attachments(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    envelope, email_message, _uid, attachments = await _load_email_message_with_attachments(
        user,
        tool_id,
        message_id,
        folder=folder,
    )
    if envelope is None and email_message is None:
        return _("Email with ID {id} not found.").format(id=message_id)
    if not attachments:
        return _("No attachments found for email {id}.").format(id=message_id)

    subject = decode_str(getattr(envelope, "subject", "") if envelope else "")
    lines = [
        _("Attachments for email {id} ({subject}):").format(
            id=message_id,
            subject=subject or _("no subject"),
        )
    ]
    lines.extend(_build_email_attachment_manifest_lines(attachments))
    return "\n".join(lines)


async def list_mailboxes(user, tool_id) -> str:
    client = await get_imap_client(user, tool_id)
    try:
        mailboxes = client.list_folders()
        result = _("Available mailboxes:\n")
        for mailbox in mailboxes:
            if isinstance(mailbox, tuple) and len(mailbox) >= 3:
                _flags, _delimiter, name = mailbox
                result += f"- {name}\n"
            else:
                result += f"- {mailbox}\n"
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
