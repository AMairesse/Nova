# nova/tools/builtins/email.py
import imapclient
import logging
import os
import smtplib
import copy
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
from email.header import decode_header

from asgiref.sync import sync_to_async  # For async-safe ORM accesses
from django.utils.translation import gettext_lazy as _

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


EMAIL_CLIENT_TIMEOUT = int(os.getenv("NOVA_EMAIL_CLIENT_TIMEOUT", "30"))


def safe_imap_logout(client):
    if client:
        try:
            client.logout()
        except Exception as error:
            logger.warning(f"IMAP logout failed: {error}")


def safe_smtp_quit(server):
    if server:
        try:
            server.quit()
        except Exception as error:
            logger.warning(f"SMTP quit failed: {error}")


def build_imap_client(credential):
    imap_server = credential.config.get('imap_server')
    imap_port = credential.config.get('imap_port', 993)
    username = credential.config.get('username')
    password = credential.config.get('password')
    use_ssl = credential.config.get('use_ssl', True)

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
    smtp_server = credential.config.get('smtp_server')
    smtp_port = credential.config.get('smtp_port', 587)
    smtp_use_tls = credential.config.get('smtp_use_tls', True)
    username = credential.config.get('username')
    password = credential.config.get('password')

    if smtp_use_tls:
        server = smtplib.SMTP(smtp_server, smtp_port, timeout=EMAIL_CLIENT_TIMEOUT)
        server.starttls()
    else:
        server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=EMAIL_CLIENT_TIMEOUT)

    server.login(username, password)
    return server


async def get_imap_client(user, tool_id):
    try:
        # Wrap ORM access in sync_to_async
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
        return build_imap_client(credential)

    except ToolCredential.DoesNotExist:
        raise ValueError(_("No IMAP credential found for tool {tool_id}").format(tool_id=tool_id))
    except Exception as e:  # Catch-all
        logger.error(f"IMAP client error: {str(e)}")
        raise


def decode_str(text):
    """Decode email header text"""
    if isinstance(text, bytes):
        text = text.decode('utf-8', errors='ignore')
    if text:
        try:
            decoded_parts = decode_header(text)
            result = ""
            for part, encoding in decoded_parts:
                if isinstance(part, bytes):
                    if encoding:
                        result += part.decode(encoding, errors='ignore')
                    else:
                        result += part.decode('utf-8', errors='ignore')
                else:
                    result += str(part)
            return result
        except Exception:
            return text  # Return as-is if decoding fails
    return ""


def safe_get(data, key):
    """Get value from dict, handling both string and bytes keys"""
    if isinstance(key, str):
        return data.get(key) or data.get(key.encode('utf-8'))
    else:
        return data.get(key) or data.get(key.decode('utf-8'))


def has_capability(client, capability):
    """Check if IMAP server supports a specific capability"""
    return capability.upper() in getattr(client, '_server_capabilities', [])


def folder_exists(client, folder_name):
    """Check if a mailbox folder exists"""
    try:
        mailboxes = client.list_folders()
        folder_names = []
        for mailbox in mailboxes:
            if isinstance(mailbox, tuple) and len(mailbox) >= 3:
                flags, delimiter, name = mailbox
                folder_names.append(name)
            else:
                folder_names.append(str(mailbox))
        return folder_name in folder_names
    except Exception:
        return False


def format_email_info(msg_id, envelope):
    """Format email information from envelope"""
    if not envelope:
        return f"ID: {msg_id} | [No envelope data]"

    subject = "[No subject]"
    if hasattr(envelope, 'subject') and envelope.subject:
        subject = decode_str(envelope.subject)

    sender = "[No sender]"
    if hasattr(envelope, 'sender') and envelope.sender:
        sender = decode_str(envelope.sender)

    date = "Unknown"
    if hasattr(envelope, 'date') and envelope.date:
        try:
            date = envelope.date.strftime('%Y-%m-%d %H:%M')
        except Exception:
            date = "Invalid date"

    return f"ID: {msg_id} | From: {sender} | Subject: {subject} | Date: {date}"


async def list_emails(user, tool_id, folder: str = "INBOX", limit: int = 10) -> str:
    """ List recent emails from specified folder. """
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)

        messages = client.search(['ALL'])
        if not messages:
            return _("No emails found in {folder}").format(folder=folder)

        # Sort by date descending and limit
        messages.sort(reverse=True)
        recent_messages = messages[:limit]

        # Fetch envelopes
        fetch_data = client.fetch(recent_messages, ['ENVELOPE'])

        result = _("Recent emails in {folder}:\n").format(folder=folder)
        for msg_id in recent_messages:
            msg_data = fetch_data.get(msg_id, {})
            envelope = safe_get(msg_data, 'ENVELOPE')
            result += format_email_info(msg_id, envelope) + "\n"

        return result
    finally:
        safe_imap_logout(client)


async def read_email(user, tool_id, message_id: int, folder: str = "INBOX", preview_only: bool = True) -> str:
    """ Read email content by message ID. Use preview_only=True for headers + content preview. """
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)

        # Use BODY.PEEK[] to avoid marking as read
        messages = client.fetch([message_id], ['ENVELOPE', 'BODY.PEEK[]'])
        if message_id not in messages:
            return _("Email with ID {id} not found.").format(id=message_id)

        msg_data = messages[message_id]
        envelope = safe_get(msg_data, 'ENVELOPE')
        body = safe_get(msg_data, 'BODY[]')

        if not body:
            return _("Email body not available.")

        # Parse email
        import email
        email_message = email.message_from_bytes(body)

        # Format response
        result = _("Email Details:\n")

        if envelope:
            sender = decode_str(getattr(envelope, 'sender', None))
            to_addr = decode_str(getattr(envelope, 'to', [None])[0] if getattr(envelope, 'to', None) else None)
            subject = decode_str(getattr(envelope, 'subject', None))
            if hasattr(envelope, 'date') and envelope.date:
                date = envelope.date.strftime('%Y-%m-%d %H:%M')
            else:
                date = "Unknown"

            result += _("From: {sender}\n").format(sender=sender)
            result += _("To: {to}\n").format(to=to_addr)
            result += _("Subject: {subject}\n").format(subject=subject)
            result += _("Date: {date}\n").format(date=date)
        else:
            result += _("From: [Not available]\nTo: [Not available]\nSubject: [Not available]\nDate: [Not available]\n")

        if preview_only:
            result += "\n" + _("Content Preview (first 500 characters):\n")
            # Extract text content preview
            content = ""
            if email_message.is_multipart():
                for part in email_message.walk():
                    if part.get_content_type() == "text/plain":
                        charset = part.get_content_charset() or 'utf-8'
                        content = part.get_payload(decode=True).decode(charset, errors='ignore')
                        break
            else:
                charset = email_message.get_content_charset() or 'utf-8'
                content = email_message.get_payload(decode=True).decode(charset, errors='ignore')

            # Truncate to 500 characters
            if len(content) > 500:
                result += content[:500] + "..."
                result += _("\n\n[Content truncated. Use preview_only=False to read full email]")
            else:
                result += content
        else:
            result += "\n" + _("Full Content:\n")
            # Extract full text content
            if email_message.is_multipart():
                for part in email_message.walk():
                    if part.get_content_type() == "text/plain":
                        charset = part.get_content_charset() or 'utf-8'
                        result += part.get_payload(decode=True).decode(charset, errors='ignore')
                        break
            else:
                charset = email_message.get_content_charset() or 'utf-8'
                result += email_message.get_payload(decode=True).decode(charset, errors='ignore')

        return result
    finally:
        safe_imap_logout(client)


async def search_emails(user, tool_id, query: str, folder: str = "INBOX", limit: int = 10) -> str:
    """ Search emails by subject or sender. """
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)

        subject_results = client.search(['SUBJECT', query])
        from_results = client.search(['FROM', query])

        all_results = list(set(subject_results + from_results))
        all_results.sort(reverse=True)
        limited_results = all_results[:limit]

        if not limited_results:
            return _("No emails found matching '{query}'").format(query=query)

        fetch_data = client.fetch(limited_results, ['ENVELOPE'])

        result = _("Emails matching '{query}':\n").format(query=query)
        for msg_id in limited_results:
            msg_data = fetch_data.get(msg_id, {})
            envelope = safe_get(msg_data, 'ENVELOPE')
            result += format_email_info(msg_id, envelope) + "\n"

        return result
    finally:
        safe_imap_logout(client)


async def list_mailboxes(user, tool_id) -> str:
    """ List all available mailbox folders. """
    client = await get_imap_client(user, tool_id)
    try:
        mailboxes = client.list_folders()

        result = _("Available mailboxes:\n")
        for mailbox in mailboxes:
            # mailbox is typically a tuple: (flags, delimiter, name)
            if isinstance(mailbox, tuple) and len(mailbox) >= 3:
                flags, delimiter, name = mailbox
                result += f"- {name}\n"
            else:
                result += f"- {mailbox}\n"

        return result
    finally:
        safe_imap_logout(client)


async def get_server_capabilities(user, tool_id) -> str:
    """ Get server capabilities and supported features. """
    client = await get_imap_client(user, tool_id)
    try:
        capabilities = getattr(client, '_server_capabilities', [])

        result = _("Server capabilities:\n")
        for cap in sorted(capabilities):
            result += f"- {cap}\n"

        # Add some derived information
        result += _("\nDerived features:\n")
        result += f"- MOVE command: {'Yes' if has_capability(client, 'MOVE') else 'No (will use COPY+DELETE)'}\n"
        result += f"- QUOTA support: {'Yes' if has_capability(client, 'QUOTA') else 'No'}\n"
        result += f"- UIDPLUS: {'Yes' if has_capability(client, 'UIDPLUS') else 'No'}\n"

        return result
    finally:
        safe_imap_logout(client)


async def send_email(user, tool_id, to: str, subject: str, body: str, cc: Optional[str] = None) -> str:
    """ Send an email via SMTP. """
    try:
        # Get SMTP configuration
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
        smtp_server = credential.config.get('smtp_server')
        username = credential.config.get('username')
        from_address = credential.config.get('from_address', username)

        if not smtp_server:
            return _("SMTP server not configured. Please add SMTP settings to your email tool configuration.")

        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_address
        msg['To'] = to
        msg['Subject'] = subject

        if cc:
            msg['Cc'] = cc

        # Add body
        msg.attach(MIMEText(body, 'plain'))

        server = None
        try:
            server = build_smtp_client(credential)

            # Send email
            recipients = [to]
            if cc:
                recipients.extend(cc.split(','))

            server.sendmail(username, recipients, msg.as_string())
        finally:
            safe_smtp_quit(server)

        # Save copy to Sent folder if configured
        sent_folder = credential.config.get('sent_folder', 'Sent')
        try:
            # Get IMAP client for saving sent email
            imap_client = await get_imap_client(user, tool_id)
            try:
                # Check if sent folder exists
                if folder_exists(imap_client, sent_folder):
                    # Add timestamp to message for sent folder
                    import email.utils
                    msg['Date'] = email.utils.formatdate()

                    # Save to sent folder
                    imap_client.append(sent_folder, msg.as_string())
                else:
                    # Try common alternative names
                    alt_names = ['Sent Items', 'Envoyés', 'Sent Messages']
                    saved = False
                    for alt_name in alt_names:
                        if folder_exists(imap_client, alt_name):
                            msg['Date'] = email.utils.formatdate()
                            imap_client.append(alt_name, msg.as_string())
                            saved = True
                            break

                    if not saved:
                        logger.warning(f"Sent folder '{sent_folder}' not found and no alternatives available")
            finally:
                safe_imap_logout(imap_client)

        except Exception as save_error:
            logger.warning(f"Failed to save email to sent folder: {save_error}")
            # Don't fail the send operation if saving to sent folder fails

        return _("Email sent successfully to {to}").format(to=to)

    except ToolCredential.DoesNotExist:
        return _("No email credential found for tool {tool_id}").format(tool_id=tool_id)


async def save_draft(user, tool_id, to: str, subject: str, body: str,
                     cc: Optional[str] = None, draft_folder: str = "Drafts") -> str:
    """ Save an email as draft in the specified folder. """
    try:
        client = await get_imap_client(user, tool_id)
        try:
            # Get from_address from credentials (fallback to username)
            credential = await sync_to_async(
                ToolCredential.objects.get, thread_sensitive=False
            )(user=user, tool_id=tool_id)
            from_address = credential.config.get('from_address', credential.config.get('username'))

            # Check if draft folder exists
            if not folder_exists(client, draft_folder):
                return _("Draft folder '{folder}' does not exist.").format(folder=draft_folder)

            # Create message
            msg = MIMEMultipart()
            msg['From'] = from_address  # From credentials
            msg['To'] = to
            msg['Subject'] = subject

            if cc:
                msg['Cc'] = cc

            # Add body
            msg.attach(MIMEText(body, 'plain'))

            # Save as draft using APPEND
            client.append(draft_folder, msg.as_string(), flags=[imapclient.DRAFT])

            return _("Draft saved successfully in {folder}").format(folder=draft_folder)
        finally:
            safe_imap_logout(client)

    except ToolCredential.DoesNotExist:
        return _("No email credential found for tool {tool_id}").format(tool_id=tool_id)


async def move_email_to_folder(user, tool_id, message_id: int,
                               source_folder: str = "INBOX", target_folder: str = "Junk") -> str:
    """ Move an email to a different folder. """
    client = await get_imap_client(user, tool_id)
    try:
        # Check if source folder exists
        if not folder_exists(client, source_folder):
            return _("Source folder '{folder}' does not exist.").format(folder=source_folder)

        client.select_folder(source_folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            return _("Email with ID {id} not found in {folder}.").format(id=message_id, folder=source_folder)

        # Check if target folder exists
        if not folder_exists(client, target_folder):
            error_msg = _("Target folder '{folder}' does not exist.").format(folder=target_folder)
            error_msg += _(" Use list_mailboxes() to see available folders.")
            return error_msg

        # Try MOVE command first (preferred)
        if has_capability(client, 'MOVE'):
            try:
                client.move([message_id], target_folder)
                msg = _("Email {id} moved from {source} to {target}.").format(
                    id=message_id, source=source_folder, target=target_folder)
                return msg
            except Exception as move_error:
                logger.warning(f"MOVE failed, trying alternative method: {move_error}")

        # Fallback: COPY + DELETE + EXPUNGE
        try:
            # Copy to target folder
            client.copy([message_id], target_folder)
            # Mark as deleted in source folder
            client.delete_messages([message_id])
            # Expunge to permanently remove
            client.expunge()

            msg = _("Email {id} moved from {source} to {target}.").format(
                id=message_id, source=source_folder, target=target_folder)
            return msg

        except Exception as fallback_error:
            logger.error(f"Fallback move method failed: {fallback_error}")
            return _("Error moving email with fallback method: {error}").format(error=str(fallback_error))
    finally:
        safe_imap_logout(client)


async def mark_email_as_read(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Mark an email as read. """
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            return _("Email with ID {id} not found.").format(id=message_id)

        # Mark as read by adding \Seen flag
        client.add_flags([message_id], [imapclient.SEEN])

        return _("Email {id} marked as read.").format(id=message_id)
    finally:
        safe_imap_logout(client)


async def mark_email_as_unread(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Mark an email as unread. """
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            return _("Email with ID {id} not found.").format(id=message_id)

        # Mark as unread by removing \Seen flag
        client.remove_flags([message_id], [imapclient.SEEN])

        return _("Email {id} marked as unread.").format(id=message_id)
    finally:
        safe_imap_logout(client)


async def delete_email(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Delete an email (move to Trash or delete permanently depending on server). """
    client = await get_imap_client(user, tool_id)
    try:
        client.select_folder(folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            return _("Email with ID {id} not found.").format(id=message_id)

        # Delete the message (server may move to Trash)
        client.delete_messages([message_id])

        return _("Email {id} deleted.").format(id=message_id)
    finally:
        safe_imap_logout(client)


async def test_email_access(user, tool_id):
    # Test IMAP connection
    imap_result = await list_emails(user, tool_id, limit=1)
    imap_success = "error" not in imap_result.lower()

    if not imap_success:
        return {"status": "error", "message": _("IMAP: %(result)s") % {"result": imap_result}}

    # Check if SMTP should be tested
    try:
        credential = await sync_to_async(
            ToolCredential.objects.get, thread_sensitive=False
        )(user=user, tool_id=tool_id)
        enable_sending = credential.config.get('enable_sending', False)
        smtp_server = credential.config.get('smtp_server')

        if enable_sending and smtp_server:
            # Test SMTP connection
            try:
                # Test SMTP with timeout and better error handling
                server = None
                try:
                    server = build_smtp_client(credential)
                finally:
                    safe_smtp_quit(server)

                # Check if sent folder exists
                sent_folder = credential.config.get('sent_folder', 'Sent')
                imap_client = await get_imap_client(user, tool_id)
                try:
                    if not folder_exists(imap_client, sent_folder):
                        # Try alternative names
                        alt_names = ['Sent Items', 'Envoyés', 'Sent Messages']
                        sent_exists = any(folder_exists(imap_client, name) for name in alt_names)

                        if not sent_exists:
                            return {
                                "status": "partial",
                                "message": _("IMAP and SMTP OK, but no sent folder found. Sent emails won't be saved.")
                            }
                finally:
                    safe_imap_logout(imap_client)
                return {
                    "status": "success",
                    "message": _("IMAP and SMTP connections successful")
                }
            except smtplib.SMTPConnectError:
                return {
                    "status": "partial",
                    "message": _("IMAP: OK, SMTP: Connection failed - check server/port")
                }
            except smtplib.SMTPAuthenticationError:
                return {
                    "status": "partial",
                    "message": _("IMAP: OK, SMTP: Authentication failed - check credentials")
                }
            except smtplib.SMTPException as e:
                return {
                    "status": "partial",
                    "message": _("IMAP: OK, SMTP: %(error)s") % {"error": str(e)}
                }
            except Exception as smtp_error:
                return {
                    "status": "partial",
                    "message": _("IMAP: OK, SMTP: Unexpected error - %(error)s") % {"error": str(smtp_error)}
                }
        else:
            return {
                "status": "success",
                "message": _("IMAP connection successful")
            }

    except ToolCredential.DoesNotExist:
        return {
            "status": "success",
            "message": _("IMAP connection successful")
        }


METADATA = {
    'name': 'Email (IMAP/SMTP)',
    'description': 'Read, send emails and manage drafts via IMAP/SMTP',
    'loading': {
        'mode': 'skill',
        'skill_id': 'mail',
        'skill_label': 'Mail',
    },
    'requires_config': True,
    'config_fields': [
        # IMAP Settings
        {'name': 'imap_server', 'type': 'text', 'label': _('IMAP Server'), 'required': True, 'group': 'imap'},
        {'name': 'imap_port', 'type': 'integer', 'label': _('IMAP Port'), 'required': False, 'default': 993,
         'group': 'imap'},
        {'name': 'use_ssl', 'type': 'boolean', 'label': _('Use SSL for IMAP'), 'required': False, 'default': True,
         'group': 'imap'},

        # Authentication
        {'name': 'username', 'type': 'text', 'label': _('Username'), 'required': True, 'group': 'auth'},
        {'name': 'password', 'type': 'password', 'label': _('Password'), 'required': True, 'group': 'auth'},

        # SMTP / Sending options
        {'name': 'enable_sending', 'type': 'boolean',
         'label': _('Enable email sending (drafts always available)'), 'required': False, 'default': False,
         'group': 'smtp'},
        {'name': 'smtp_server', 'type': 'text', 'label': _('SMTP Server'), 'required': False, 'group': 'smtp',
         'visible_if': {'field': 'enable_sending', 'equals': True}},
        {'name': 'smtp_port', 'type': 'integer', 'label': _('SMTP Port'), 'required': False, 'default': 587,
         'group': 'smtp', 'visible_if': {'field': 'enable_sending', 'equals': True}},
        {'name': 'smtp_use_tls', 'type': 'boolean', 'label': _('Use TLS for SMTP'), 'required': False, 'default': True,
         'group': 'smtp', 'visible_if': {'field': 'enable_sending', 'equals': True}},
        {'name': 'from_address', 'type': 'text', 'label': _('From Address (needed if username is not an email)'),
         'required': False, 'group': 'smtp', 'visible_if': {'field': 'enable_sending', 'equals': True}},
        {'name': 'sent_folder', 'type': 'text', 'label': _('Sent Folder Name (to move sent emails to)'),
         'required': False, 'default': 'Sent', 'group': 'smtp',
         'visible_if': {'field': 'enable_sending', 'equals': True}},
    ],
    'test_function': 'test_email_access',
    'test_function_args': ['user', 'tool_id'],
}


def get_skill_instructions(agent=None, tools=None) -> list[str]:
    return [
        "Use preview-oriented reads first to keep context compact; read full content only when necessary.",
        "Never send an email when key details are missing (recipient, subject, or message body intent). Ask first.",
        "Use list_mailboxes before bulk organization to avoid invalid folder names and unintended moves.",
    ]


AGGREGATION_SPEC = {
    "min_instances": 2,
}


_EMAIL_TOOL_SPECS = [
    (
        "list_emails",
        "List recent emails from a mailbox folder",
        {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "mailbox folder name",
                    "default": "INBOX",
                },
                "limit": {
                    "type": "integer",
                    "description": "maximum number of emails to return",
                    "default": 10,
                },
            },
            "required": [],
        },
    ),
    (
        "read_email",
        "Read the full content of an email by its message ID",
        {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "IMAP message ID",
                },
                "folder": {
                    "type": "string",
                    "description": "mailbox folder name",
                    "default": "INBOX",
                },
            },
            "required": ["message_id"],
        },
    ),
    (
        "search_emails",
        "Search emails by subject or sender",
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "search term",
                },
                "folder": {
                    "type": "string",
                    "description": "mailbox folder name",
                    "default": "INBOX",
                },
                "limit": {
                    "type": "integer",
                    "description": "maximum number of results",
                    "default": 10,
                },
            },
            "required": ["query"],
        },
    ),
    (
        "list_mailboxes",
        "List all available mailbox folders on the email server",
        {
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    (
        "move_email_to_folder",
        "Move an email to a different folder (useful for marking as spam)",
        {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "IMAP message ID to move",
                },
                "source_folder": {
                    "type": "string",
                    "description": "source mailbox folder name",
                    "default": "INBOX",
                },
                "target_folder": {
                    "type": "string",
                    "description": "target mailbox folder name",
                    "default": "Junk",
                },
            },
            "required": ["message_id"],
        },
    ),
    (
        "mark_email_as_read",
        "Mark an email as read",
        {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "IMAP message ID",
                },
                "folder": {
                    "type": "string",
                    "description": "mailbox folder name",
                    "default": "INBOX",
                },
            },
            "required": ["message_id"],
        },
    ),
    (
        "mark_email_as_unread",
        "Mark an email as unread",
        {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "IMAP message ID",
                },
                "folder": {
                    "type": "string",
                    "description": "mailbox folder name",
                    "default": "INBOX",
                },
            },
            "required": ["message_id"],
        },
    ),
    (
        "delete_email",
        "Delete an email",
        {
            "type": "object",
            "properties": {
                "message_id": {
                    "type": "integer",
                    "description": "IMAP message ID",
                },
                "folder": {
                    "type": "string",
                    "description": "mailbox folder name",
                    "default": "INBOX",
                },
            },
            "required": ["message_id"],
        },
    ),
    (
        "get_server_capabilities",
        "Get IMAP server capabilities and supported features",
        {
            "type": "object",
            "properties": {},
            "required": [],
        },
    ),
    (
        "send_email",
        "Send an email via SMTP",
        {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "email subject",
                },
                "body": {
                    "type": "string",
                    "description": "email body content",
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients (comma-separated)",
                    "default": None,
                },
            },
            "required": ["to", "subject", "body"],
        },
    ),
    (
        "save_draft",
        "Save an email as draft in the specified folder",
        {
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": "recipient email address",
                },
                "subject": {
                    "type": "string",
                    "description": "email subject",
                },
                "body": {
                    "type": "string",
                    "description": "email body content",
                },
                "cc": {
                    "type": "string",
                    "description": "CC recipients (comma-separated)",
                    "default": None,
                },
                "draft_folder": {
                    "type": "string",
                    "description": "folder to save draft in",
                    "default": "Drafts",
                },
            },
            "required": ["to", "subject", "body"],
        },
    ),
]


def _mailbox_prefix(alias: str, credential: ToolCredential | None) -> str:
    alias = (alias or "").strip() or "Email"
    account = ""
    if credential:
        cfg = credential.config or {}
        account = cfg.get("from_address") or cfg.get("username") or ""
    if account:
        return f"[Mailbox: {alias}; account: {account}]"
    return f"[Mailbox: {alias}]"


def _with_mailbox_selector(args_schema: dict, mailbox_schema: dict | None) -> dict:
    schema = copy.deepcopy(args_schema)
    if not mailbox_schema:
        return schema

    properties = dict(schema.get("properties") or {})
    schema["properties"] = {"mailbox": copy.deepcopy(mailbox_schema), **properties}
    required = list(schema.get("required") or [])
    if "mailbox" not in required:
        required.insert(0, "mailbox")
    schema["required"] = required
    return schema


def _build_toolset(
    *,
    wrappers: dict[str, object],
    description_prefix: str = "",
    mailbox_schema: dict | None = None,
) -> list[StructuredTool]:
    result: list[StructuredTool] = []
    for name, description, args_schema in _EMAIL_TOOL_SPECS:
        full_description = f"{description_prefix} {description}".strip()
        result.append(
            StructuredTool.from_function(
                coroutine=wrappers[name],
                name=name,
                description=full_description,
                args_schema=_with_mailbox_selector(args_schema, mailbox_schema),
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


def _mailbox_account(credential: ToolCredential | None) -> str:
    if not credential:
        return ""
    cfg = credential.config or {}
    return (cfg.get("from_address") or cfg.get("username") or "").strip()


async def _build_mailbox_registry(tools: list[Tool], agent: LLMAgent) -> tuple:
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
                "can_send": bool(config.get("enable_sending", False)),
            }
        )

    if not entries:
        raise ValueError("No configured email mailbox available for aggregation.")

    aliases = [entry["alias"] for entry in entries]
    lookup = {normalize_instance_key(entry["alias"]): entry for entry in entries}
    mailbox_schema = build_selector_schema(
        selector_name="mailbox",
        labels=aliases,
        description=f"Mailbox alias to use. Available aliases: {', '.join(aliases)}.",
    )
    return user, entries, lookup, mailbox_schema


def _resolve_mailbox(mailbox: str, lookup: dict, aliases: list[str]) -> tuple:
    entry = lookup.get(normalize_instance_key(mailbox))
    if entry:
        return entry, None
    return None, format_invalid_instance_message(
        selector_name="mailbox",
        value=mailbox,
        available_labels=aliases,
    )


async def get_aggregated_functions(tools: list[Tool], agent: LLMAgent) -> List[StructuredTool]:
    user, entries, lookup, mailbox_schema = await _build_mailbox_registry(tools, agent)
    aliases = [entry["alias"] for entry in entries]
    description_prefix = "[Multi-mailbox email, select a mailbox alias.]"

    async def list_emails_wrapper(mailbox: str, folder: str = "INBOX", limit: int = 10) -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await list_emails(user, entry["tool_id"], folder, limit)

    async def read_email_wrapper(mailbox: str, message_id: int, folder: str = "INBOX") -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await read_email(user, entry["tool_id"], message_id, folder)

    async def search_emails_wrapper(
        mailbox: str,
        query: str,
        folder: str = "INBOX",
        limit: int = 10,
    ) -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await search_emails(user, entry["tool_id"], query, folder, limit)

    async def list_mailboxes_wrapper(mailbox: str) -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await list_mailboxes(user, entry["tool_id"])

    async def move_email_to_folder_wrapper(
        mailbox: str,
        message_id: int,
        source_folder: str = "INBOX",
        target_folder: str = "Junk",
    ) -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await move_email_to_folder(
            user,
            entry["tool_id"],
            message_id,
            source_folder,
            target_folder,
        )

    async def mark_email_as_read_wrapper(mailbox: str, message_id: int, folder: str = "INBOX") -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await mark_email_as_read(user, entry["tool_id"], message_id, folder)

    async def mark_email_as_unread_wrapper(mailbox: str, message_id: int, folder: str = "INBOX") -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await mark_email_as_unread(user, entry["tool_id"], message_id, folder)

    async def delete_email_wrapper(mailbox: str, message_id: int, folder: str = "INBOX") -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await delete_email(user, entry["tool_id"], message_id, folder)

    async def get_server_capabilities_wrapper(mailbox: str) -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await get_server_capabilities(user, entry["tool_id"])

    async def send_email_wrapper(
        mailbox: str,
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
    ) -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        if not entry["can_send"]:
            return _(
                "Sending is disabled for mailbox '{mailbox}'. Enable sending in this mailbox configuration."
            ).format(mailbox=entry["alias"])
        return await send_email(user, entry["tool_id"], to, subject, body, cc)

    async def save_draft_wrapper(
        mailbox: str,
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
        draft_folder: str = "Drafts",
    ) -> str:
        entry, err = _resolve_mailbox(mailbox, lookup, aliases)
        if err:
            return err
        return await save_draft(user, entry["tool_id"], to, subject, body, cc, draft_folder)

    wrappers = {
        "list_emails": list_emails_wrapper,
        "read_email": read_email_wrapper,
        "search_emails": search_emails_wrapper,
        "list_mailboxes": list_mailboxes_wrapper,
        "move_email_to_folder": move_email_to_folder_wrapper,
        "mark_email_as_read": mark_email_as_read_wrapper,
        "mark_email_as_unread": mark_email_as_unread_wrapper,
        "delete_email": delete_email_wrapper,
        "get_server_capabilities": get_server_capabilities_wrapper,
        "send_email": send_email_wrapper,
        "save_draft": save_draft_wrapper,
    }
    return _build_toolset(
        wrappers=wrappers,
        description_prefix=description_prefix,
        mailbox_schema=mailbox_schema,
    )


async def get_aggregated_prompt_instructions(tools: list[Tool], agent: LLMAgent) -> List[str]:
    try:
        _, entries, _, _ = await _build_mailbox_registry(tools, agent)
    except Exception as e:
        logger.warning("Could not build aggregated email prompt instructions: %s", str(e))
        return []

    mailbox_parts = []
    for entry in entries:
        account_part = f", account: {entry['account']}" if entry["account"] else ""
        send_part = "enabled" if entry["can_send"] else "disabled"
        mailbox_parts.append(f"{entry['alias']} (sending: {send_part}{account_part})")

    return [
        f"Email mailbox map: {'; '.join(mailbox_parts)}.",
        "When multiple mailboxes are plausible, ask which mailbox to use.",
        "Do not send emails from a mailbox where sending is disabled.",
        "Reuse the current mailbox in the same workflow unless the user asks to switch.",
    ]


async def get_functions(tool: Tool, agent: LLMAgent) -> List[StructuredTool]:
    """Legacy single-mailbox email toolset."""
    has_required_data = await sync_to_async(
        lambda: bool(tool and tool.id),
        thread_sensitive=False,
    )()
    if not has_required_data:
        raise ValueError("Tool instance missing required data (id).")

    user = await _resolve_user_for_tool(tool, agent)
    if not user:
        raise ValueError("Tool instance missing required data (user).")

    tool_id = await sync_to_async(lambda: tool.id, thread_sensitive=False)()
    alias = await sync_to_async(lambda: tool.name, thread_sensitive=False)()
    credential = await _get_credential(user, tool_id)
    description_prefix = _mailbox_prefix(alias, credential)

    async def list_emails_wrapper(folder: str = "INBOX", limit: int = 10) -> str:
        return await list_emails(user, tool_id, folder, limit)

    async def read_email_wrapper(message_id: int, folder: str = "INBOX") -> str:
        return await read_email(user, tool_id, message_id, folder)

    async def search_emails_wrapper(query: str, folder: str = "INBOX", limit: int = 10) -> str:
        return await search_emails(user, tool_id, query, folder, limit)

    async def list_mailboxes_wrapper() -> str:
        return await list_mailboxes(user, tool_id)

    async def move_email_to_folder_wrapper(
        message_id: int,
        source_folder: str = "INBOX",
        target_folder: str = "Junk",
    ) -> str:
        return await move_email_to_folder(
            user,
            tool_id,
            message_id,
            source_folder,
            target_folder,
        )

    async def mark_email_as_read_wrapper(message_id: int, folder: str = "INBOX") -> str:
        return await mark_email_as_read(user, tool_id, message_id, folder)

    async def mark_email_as_unread_wrapper(message_id: int, folder: str = "INBOX") -> str:
        return await mark_email_as_unread(user, tool_id, message_id, folder)

    async def delete_email_wrapper(message_id: int, folder: str = "INBOX") -> str:
        return await delete_email(user, tool_id, message_id, folder)

    async def get_server_capabilities_wrapper() -> str:
        return await get_server_capabilities(user, tool_id)

    async def send_email_wrapper(to: str, subject: str, body: str, cc: Optional[str] = None) -> str:
        return await send_email(user, tool_id, to, subject, body, cc)

    async def save_draft_wrapper(
        to: str,
        subject: str,
        body: str,
        cc: Optional[str] = None,
        draft_folder: str = "Drafts",
    ) -> str:
        return await save_draft(user, tool_id, to, subject, body, cc, draft_folder)

    wrappers = {
        "list_emails": list_emails_wrapper,
        "read_email": read_email_wrapper,
        "search_emails": search_emails_wrapper,
        "list_mailboxes": list_mailboxes_wrapper,
        "move_email_to_folder": move_email_to_folder_wrapper,
        "mark_email_as_read": mark_email_as_read_wrapper,
        "mark_email_as_unread": mark_email_as_unread_wrapper,
        "delete_email": delete_email_wrapper,
        "get_server_capabilities": get_server_capabilities_wrapper,
        "send_email": send_email_wrapper,
        "save_draft": save_draft_wrapper,
    }
    return _build_toolset(
        wrappers=wrappers,
        description_prefix=description_prefix,
    )
