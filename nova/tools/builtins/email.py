# nova/tools/builtins/email.py
import imapclient
import logging
from typing import List
from email.header import decode_header

from asgiref.sync import sync_to_async  # For async-safe ORM accesses
from django.utils.translation import gettext_lazy as _

from langchain_core.tools import StructuredTool

from nova.llm.llm_agent import LLMAgent
from nova.models.Tool import Tool, ToolCredential


logger = logging.getLogger(__name__)


async def get_imap_client(user, tool_id):
    try:
        # Wrap ORM access in sync_to_async
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
        imap_server = credential.config.get('imap_server')
        imap_port = credential.config.get('imap_port', 993)
        username = credential.config.get('username')
        password = credential.config.get('password')
        use_ssl = credential.config.get('use_ssl', True)

        if not all([imap_server, username, password]):
            raise ValueError(_("Incomplete IMAP configuration: missing server, username, or password"))

        client = imapclient.IMAPClient(imap_server, port=imap_port, ssl=use_ssl)
        client.login(username, password)

        return client

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
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        messages = client.search(['ALL'])
        if not messages:
            client.logout()
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

        client.logout()
        return result

    except Exception as e:
        logger.error(f"Error in list_emails: {e}")
        return _("Error retrieving emails: {error}").format(error=str(e))


async def read_email(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Read full email content by message ID. """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        messages = client.fetch([message_id], ['ENVELOPE', 'BODY[]'])
        if message_id not in messages:
            client.logout()
            return _("Email with ID {id} not found.").format(id=message_id)

        msg_data = messages[message_id]
        envelope = safe_get(msg_data, 'ENVELOPE')
        body = safe_get(msg_data, 'BODY[]')

        if not body:
            client.logout()
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

        result += "\n" + _("Content:\n")

        # Extract text content
        if email_message.is_multipart():
            for part in email_message.walk():
                if part.get_content_type() == "text/plain":
                    charset = part.get_content_charset() or 'utf-8'
                    result += part.get_payload(decode=True).decode(charset, errors='ignore')
                    break
        else:
            charset = email_message.get_content_charset() or 'utf-8'
            result += email_message.get_payload(decode=True).decode(charset, errors='ignore')

        client.logout()
        return result

    except Exception as e:
        logger.error(f"Error in read_email: {e}")
        return _("Error reading email: {error}").format(error=str(e))


async def search_emails(user, tool_id, query: str, folder: str = "INBOX", limit: int = 10) -> str:
    """ Search emails by subject or sender. """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        subject_results = client.search(['SUBJECT', query])
        from_results = client.search(['FROM', query])

        all_results = list(set(subject_results + from_results))
        all_results.sort(reverse=True)
        limited_results = all_results[:limit]

        if not limited_results:
            client.logout()
            return _("No emails found matching '{query}'").format(query=query)

        fetch_data = client.fetch(limited_results, ['ENVELOPE'])

        result = _("Emails matching '{query}':\n").format(query=query)
        for msg_id in limited_results:
            msg_data = fetch_data.get(msg_id, {})
            envelope = safe_get(msg_data, 'ENVELOPE')
            result += format_email_info(msg_id, envelope) + "\n"

        client.logout()
        return result

    except Exception as e:
        logger.error(f"Error in search_emails: {e}")
        return _("Error searching emails: {error}").format(error=str(e))


async def test_imap_access(user, tool_id):
    try:
        result = await list_emails(user, tool_id, limit=1)
        if "error" in result.lower():
            return {"status": "error", "message": result}
        else:
            return {
                "status": "success",
                "message": _("IMAP connection successful")
            }
    except Exception as e:
        return {
            "status": "error",
            "message": _("Connection error: %(err)s") % {"err": e}
        }


METADATA = {
    'name': 'Email (IMAP)',
    'description': 'Read emails from IMAP server',
    'requires_config': True,
    'config_fields': [
        {'name': 'imap_server', 'type': 'text', 'label': _('IMAP Server'), 'required': True},
        {'name': 'imap_port', 'type': 'integer', 'label': _('IMAP Port'), 'required': False, 'default': 993},
        {'name': 'username', 'type': 'text', 'label': _('Username'), 'required': True},
        {'name': 'password', 'type': 'password', 'label': _('Password'), 'required': True},
        {'name': 'use_ssl', 'type': 'boolean', 'label': _('Use SSL'), 'required': False, 'default': True},
    ],
    'test_function': test_imap_access,
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

    # Create wrapper functions as langchain 1.1 does not support partial() anymore
    async def list_emails_wrapper(folder: str = "INBOX", limit: int = 10) -> str:
        return await list_emails(user, tool_id, folder, limit)

    async def read_email_wrapper(message_id: int, folder: str = "INBOX") -> str:
        return await read_email(user, tool_id, message_id, folder)

    async def search_emails_wrapper(query: str, folder: str = "INBOX", limit: int = 10) -> str:
        return await search_emails(user, tool_id, query, folder, limit)

    return [
        StructuredTool.from_function(
            coroutine=list_emails_wrapper,
            name="list_emails",
            description="List recent emails from a mailbox folder",
            args_schema={
                "type": "object",
                "properties": {
                    "folder": {
                        "type": "string",
                        "description": "mailbox folder name",
                        "default": "INBOX"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "maximum number of emails to return",
                        "default": 10
                    }
                },
                "required": []
            }
        ),
        StructuredTool.from_function(
            coroutine=read_email_wrapper,
            name="read_email",
            description="Read the full content of an email by its message ID",
            args_schema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "IMAP message ID"
                    },
                    "folder": {
                        "type": "string",
                        "description": "mailbox folder name",
                        "default": "INBOX"
                    }
                },
                "required": ["message_id"]
            }
        ),
        StructuredTool.from_function(
            coroutine=search_emails_wrapper,
            name="search_emails",
            description="Search emails by subject or sender",
            args_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "search term"
                    },
                    "folder": {
                        "type": "string",
                        "description": "mailbox folder name",
                        "default": "INBOX"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "maximum number of results",
                        "default": 10
                    }
                },
                "required": ["query"]
            }
        )
    ]
