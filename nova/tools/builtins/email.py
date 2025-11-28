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
    if text:
        decoded_parts = decode_header(text)
        result = ""
        for part, encoding in decoded_parts:
            if isinstance(part, bytes):
                if encoding:
                    result += part.decode(encoding)
                else:
                    result += part.decode('utf-8', errors='ignore')
            else:
                result += str(part)
        return result
    return ""


def format_email_summary(msg_id, envelope):
    """Format email envelope as summary string"""
    if not envelope:
        return f"ID: {msg_id} | [Envelope not available]"

    subject = decode_str(envelope.subject) if envelope.subject else "[No subject]"
    sender = decode_str(envelope.sender) if envelope.sender else "[No sender]"
    date = envelope.date.strftime('%Y-%m-%d %H:%M') if envelope.date else "Unknown"
    return f"ID: {msg_id} | From: {sender} | Subject: {subject} | Date: {date}"


async def list_emails(user, tool_id, folder: str = "INBOX", limit: int = 10) -> str:
    """ List recent emails from specified folder.
    Args:
        user: the Django user
        tool_id: ID of the email tool
        folder: mailbox folder name (default: INBOX)
        limit: maximum number of emails to return (default: 10)

    Returns:
        Formatted list of email summaries
    """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        # Get recent message IDs
        messages = client.search(['ALL'])
        if not messages:
            client.logout()
            return _("No emails found in {folder}").format(folder=folder)

        # Sort by date descending and limit
        messages.sort(reverse=True)
        recent_messages = messages[:limit]

        # Fetch envelopes
        envelopes = client.fetch(recent_messages, ['ENVELOPE'])

        result = _("Recent emails in {folder}:\n").format(folder=folder)
        for msg_id in recent_messages:
            if msg_id in envelopes and 'ENVELOPE' in envelopes[msg_id]:
                envelope = envelopes[msg_id]['ENVELOPE']
                result += format_email_summary(msg_id, envelope) + "\n"
            else:
                result += f"ID: {msg_id} | [Envelope not available]\n"

        client.logout()
        return result

    except (ValueError, imapclient.IMAPClient.Error) as e:
        return _("IMAP error: {error}. Check credentials and server settings.").format(error=str(e))
    except Exception as e:
        return _("Unexpected error when retrieving emails: {error}").format(error=str(e))


async def read_email(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Read full email content by message ID.
    Args:
        user: the Django user
        tool_id: ID of the email tool
        message_id: IMAP message ID
        folder: mailbox folder name (default: INBOX)

    Returns:
        Formatted email content
    """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        # Fetch message
        messages = client.fetch([message_id], ['ENVELOPE', 'BODY[]'])
        if message_id not in messages:
            client.logout()
            return _("Email with ID {id} not found.").format(id=message_id)

        msg_data = messages[message_id]
        envelope = msg_data.get('ENVELOPE')
        body = msg_data.get('BODY[]')

        if not body:
            client.logout()
            return _("Email body not available.")

        # Parse email
        import email
        email_message = email.message_from_bytes(body)

        # Format response
        result = _("Email Details:\n")

        if envelope:
            result += _("From: {sender}\n").format(sender=decode_str(envelope.sender))
            result += _("To: {to}\n").format(to=decode_str(envelope.to[0]) if envelope.to else "Unknown")
            result += _("Subject: {subject}\n").format(subject=decode_str(envelope.subject))
            date_str = envelope.date.strftime('%Y-%m-%d %H:%M') if envelope.date else "Unknown"
            result += _("Date: {date}\n").format(date=date_str)
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

    except (ValueError, imapclient.IMAPClient.Error) as e:
        return _("IMAP error: {error}. Check credentials and server settings.").format(error=str(e))
    except Exception as e:
        return _("Unexpected error when reading email: {error}").format(error=str(e))


async def search_emails(user, tool_id, query: str, folder: str = "INBOX", limit: int = 10) -> str:
    """ Search emails by subject or sender.
    Args:
        user: the Django user
        tool_id: ID of the email tool
        query: search term
        folder: mailbox folder name (default: INBOX)
        limit: maximum number of results (default: 10)

    Returns:
        Formatted list of matching emails
    """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        # Search by subject or from
        subject_criteria = ['SUBJECT', query]
        from_criteria = ['FROM', query]

        subject_results = client.search(subject_criteria)
        from_results = client.search(from_criteria)

        # Combine and deduplicate
        all_results = list(set(subject_results + from_results))
        all_results.sort(reverse=True)
        limited_results = all_results[:limit]

        if not limited_results:
            client.logout()
            return _("No emails found matching '{query}'").format(query=query)

        # Fetch envelopes
        envelopes = client.fetch(limited_results, ['ENVELOPE'])

        result = _("Emails matching '{query}':\n").format(query=query)
        for msg_id in limited_results:
            if msg_id in envelopes and 'ENVELOPE' in envelopes[msg_id]:
                envelope = envelopes[msg_id]['ENVELOPE']
                result += format_email_summary(msg_id, envelope) + "\n"
            else:
                result += f"ID: {msg_id} | [Envelope not available]\n"

        client.logout()
        return result

    except (ValueError, imapclient.IMAPClient.Error) as e:
        return _("IMAP error: {error}. Check credentials and server settings.").format(error=str(e))
    except Exception as e:
        return _("Unexpected error when searching emails: {error}").format(error=str(e))


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
