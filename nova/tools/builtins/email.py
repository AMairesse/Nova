# nova/tools/builtins/email.py
import imapclient
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Optional
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

        # Store server capabilities for later use
        client._server_capabilities = client.capabilities()

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


async def read_email(user, tool_id, message_id: int, folder: str = "INBOX", preview_only: bool = True) -> str:
    """ Read email content by message ID. Use preview_only=True for headers + content preview. """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        # Use BODY.PEEK[] to avoid marking as read
        messages = client.fetch([message_id], ['ENVELOPE', 'BODY.PEEK[]'])
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


async def list_mailboxes(user, tool_id) -> str:
    """ List all available mailbox folders. """
    try:
        client = await get_imap_client(user, tool_id)

        mailboxes = client.list_folders()

        result = _("Available mailboxes:\n")
        for mailbox in mailboxes:
            # mailbox is typically a tuple: (flags, delimiter, name)
            if isinstance(mailbox, tuple) and len(mailbox) >= 3:
                flags, delimiter, name = mailbox
                result += f"- {name}\n"
            else:
                result += f"- {mailbox}\n"

        client.logout()
        return result

    except Exception as e:
        logger.error(f"Error in list_mailboxes: {e}")
        return _("Error listing mailboxes: {error}").format(error=str(e))


async def get_server_capabilities(user, tool_id) -> str:
    """ Get server capabilities and supported features. """
    try:
        client = await get_imap_client(user, tool_id)

        capabilities = getattr(client, '_server_capabilities', [])

        result = _("Server capabilities:\n")
        for cap in sorted(capabilities):
            result += f"- {cap}\n"

        # Add some derived information
        result += _("\nDerived features:\n")
        result += f"- MOVE command: {'Yes' if has_capability(client, 'MOVE') else 'No (will use COPY+DELETE)'}\n"
        result += f"- QUOTA support: {'Yes' if has_capability(client, 'QUOTA') else 'No'}\n"
        result += f"- UIDPLUS: {'Yes' if has_capability(client, 'UIDPLUS') else 'No'}\n"

        client.logout()
        return result

    except Exception as e:
        logger.error(f"Error in get_server_capabilities: {e}")
        return _("Error getting server capabilities: {error}").format(error=str(e))


async def send_email(user, tool_id, to: str, subject: str, body: str, cc: Optional[str] = None) -> str:
    """ Send an email via SMTP. """
    try:
        # Get SMTP configuration
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
        smtp_server = credential.config.get('smtp_server')
        smtp_port = credential.config.get('smtp_port', 587)
        username = credential.config.get('username')
        password = credential.config.get('password')
        smtp_use_tls = credential.config.get('smtp_use_tls', True)

        if not smtp_server:
            return _("SMTP server not configured. Please add SMTP settings to your email tool configuration.")

        # Create message
        msg = MIMEMultipart()
        msg['From'] = username
        msg['To'] = to
        msg['Subject'] = subject

        if cc:
            msg['Cc'] = cc

        # Add body
        msg.attach(MIMEText(body, 'plain'))

        # Connect to SMTP server
        if smtp_use_tls:
            server = smtplib.SMTP(smtp_server, smtp_port)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_server, smtp_port)

        server.login(username, password)

        # Send email
        recipients = [to]
        if cc:
            recipients.extend(cc.split(','))

        server.sendmail(username, recipients, msg.as_string())
        server.quit()

        return _("Email sent successfully to {to}").format(to=to)

    except ToolCredential.DoesNotExist:
        return _("No email credential found for tool {tool_id}").format(tool_id=tool_id)
    except Exception as e:
        logger.error(f"Error in send_email: {e}")
        return _("Error sending email: {error}").format(error=str(e))


async def save_draft(user, tool_id, to: str, subject: str, body: str,
                     cc: Optional[str] = None, draft_folder: str = "Drafts") -> str:
    """ Save an email as draft in the specified folder. """
    try:
        client = await get_imap_client(user, tool_id)

        # Get username from credentials
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
        username = credential.config.get('username')

        # Check if draft folder exists
        if not folder_exists(client, draft_folder):
            client.logout()
            return _("Draft folder '{folder}' does not exist.").format(folder=draft_folder)

        # Create message
        msg = MIMEMultipart()
        msg['From'] = username  # From credentials
        msg['To'] = to
        msg['Subject'] = subject

        if cc:
            msg['Cc'] = cc

        # Add body
        msg.attach(MIMEText(body, 'plain'))

        # Save as draft using APPEND
        client.append(draft_folder, msg.as_string(), flags=[imapclient.DRAFT])

        client.logout()
        return _("Draft saved successfully in {folder}").format(folder=draft_folder)

    except ToolCredential.DoesNotExist:
        return _("No email credential found for tool {tool_id}").format(tool_id=tool_id)
    except Exception as e:
        logger.error(f"Error in save_draft: {e}")
        return _("Error saving draft: {error}").format(error=str(e))


async def move_email_to_folder(user, tool_id, message_id: int,
                               source_folder: str = "INBOX", target_folder: str = "Junk") -> str:
    """ Move an email to a different folder. """
    try:
        client = await get_imap_client(user, tool_id)

        # Check if source folder exists
        if not folder_exists(client, source_folder):
            client.logout()
            return _("Source folder '{folder}' does not exist.").format(folder=source_folder)

        client.select_folder(source_folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            client.logout()
            return _("Email with ID {id} not found in {folder}.").format(id=message_id, folder=source_folder)

        # Check if target folder exists
        if not folder_exists(client, target_folder):
            client.logout()
            error_msg = _("Target folder '{folder}' does not exist.").format(folder=target_folder)
            error_msg += _(" Use list_mailboxes() to see available folders.")
            return error_msg

        # Try MOVE command first (preferred)
        if has_capability(client, 'MOVE'):
            try:
                client.move([message_id], target_folder)
                client.logout()
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

            client.logout()
            msg = _("Email {id} moved from {source} to {target}.").format(
                id=message_id, source=source_folder, target=target_folder)
            return msg

        except Exception as fallback_error:
            client.logout()
            logger.error(f"Fallback move method failed: {fallback_error}")
            return _("Error moving email with fallback method: {error}").format(error=str(fallback_error))

    except Exception as e:
        logger.error(f"Error in move_email_to_folder: {e}")
        return _("Error moving email: {error}").format(error=str(e))


async def mark_email_as_read(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Mark an email as read. """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            client.logout()
            return _("Email with ID {id} not found.").format(id=message_id)

        # Mark as read by adding \Seen flag
        client.add_flags([message_id], [imapclient.SEEN])

        client.logout()
        return _("Email {id} marked as read.").format(id=message_id)

    except Exception as e:
        logger.error(f"Error in mark_email_as_read: {e}")
        return _("Error marking email as read: {error}").format(error=str(e))


async def mark_email_as_unread(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Mark an email as unread. """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            client.logout()
            return _("Email with ID {id} not found.").format(id=message_id)

        # Mark as unread by removing \Seen flag
        client.remove_flags([message_id], [imapclient.SEEN])

        client.logout()
        return _("Email {id} marked as unread.").format(id=message_id)

    except Exception as e:
        logger.error(f"Error in mark_email_as_unread: {e}")
        return _("Error marking email as unread: {error}").format(error=str(e))


async def delete_email(user, tool_id, message_id: int, folder: str = "INBOX") -> str:
    """ Delete an email (move to Trash or delete permanently depending on server). """
    try:
        client = await get_imap_client(user, tool_id)
        client.select_folder(folder)

        # Check if message exists
        messages = client.fetch([message_id], ['ENVELOPE'])
        if message_id not in messages:
            client.logout()
            return _("Email with ID {id} not found.").format(id=message_id)

        # Delete the message (server may move to Trash)
        client.delete_messages([message_id])

        client.logout()
        return _("Email {id} deleted.").format(id=message_id)

    except Exception as e:
        logger.error(f"Error in delete_email: {e}")
        return _("Error deleting email: {error}").format(error=str(e))


async def test_imap_access(user, tool_id):
    try:
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
                smtp_port = credential.config.get('smtp_port', 587)
                smtp_use_tls = credential.config.get('smtp_use_tls', True)
                username = credential.config.get('username')
                password = credential.config.get('password')

                try:
                    # Test SMTP with timeout and better error handling
                    if smtp_use_tls:
                        server = smtplib.SMTP(smtp_server, smtp_port, timeout=5)
                        server.starttls()
                    else:
                        server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=5)

                    server.login(username, password)
                    server.quit()

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

    except Exception as e:
        return {
            "status": "error",
            "message": _("Connection error: %(err)s") % {"err": e}
        }


METADATA = {
    'name': 'Email (IMAP/SMTP)',
    'description': 'Read, send emails and manage drafts via IMAP/SMTP',
    'requires_config': True,
    'config_fields': [
        {'name': 'imap_server', 'type': 'text', 'label': _('IMAP Server'), 'required': True},
        {'name': 'imap_port', 'type': 'integer', 'label': _('IMAP Port'), 'required': False, 'default': 993},
        {'name': 'smtp_server', 'type': 'text', 'label': _('SMTP Server'), 'required': False},
        {'name': 'smtp_port', 'type': 'integer', 'label': _('SMTP Port'), 'required': False, 'default': 587},
        {'name': 'username', 'type': 'text', 'label': _('Username'), 'required': True},
        {'name': 'password', 'type': 'password', 'label': _('Password'), 'required': True},
        {'name': 'use_ssl', 'type': 'boolean', 'label': _('Use SSL for IMAP'), 'required': False, 'default': True},
        {'name': 'enable_sending', 'type': 'boolean',
         'label': _('Enable email sending (drafts always available)'), 'required': False, 'default': False},
        {'name': 'smtp_server', 'type': 'text', 'label': _('SMTP Server'), 'required': False},
        {'name': 'smtp_port', 'type': 'integer', 'label': _('SMTP Port'), 'required': False, 'default': 587},
        {'name': 'smtp_use_tls', 'type': 'boolean', 'label': _('Use TLS for SMTP'), 'required': False, 'default': True},
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

    # Check if sending is enabled
    enable_sending = False
    try:
        credential = await sync_to_async(ToolCredential.objects.get, thread_sensitive=False)(user=user, tool_id=tool_id)
        enable_sending = credential.config.get('enable_sending', False)
    except ToolCredential.DoesNotExist:
        pass  # Will use default False

    # Create wrapper functions as langchain 1.1 does not support partial() anymore
    async def list_emails_wrapper(folder: str = "INBOX", limit: int = 10) -> str:
        return await list_emails(user, tool_id, folder, limit)

    async def read_email_wrapper(message_id: int, folder: str = "INBOX") -> str:
        return await read_email(user, tool_id, message_id, folder)

    async def search_emails_wrapper(query: str, folder: str = "INBOX", limit: int = 10) -> str:
        return await search_emails(user, tool_id, query, folder, limit)

    async def list_mailboxes_wrapper() -> str:
        return await list_mailboxes(user, tool_id)

    async def move_email_to_folder_wrapper(message_id: int,
                                           source_folder: str = "INBOX",
                                           target_folder: str = "Junk") -> str:
        return await move_email_to_folder(user, tool_id, message_id, source_folder, target_folder)

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

    async def save_draft_wrapper(to: str, subject: str, body: str,
                                 cc: Optional[str] = None, draft_folder: str = "Drafts") -> str:
        return await save_draft(user, tool_id, to, subject, body, cc, draft_folder)

    # Base tools always available
    tools = [
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
        ),
        StructuredTool.from_function(
            coroutine=list_mailboxes_wrapper,
            name="list_mailboxes",
            description="List all available mailbox folders on the email server",
            args_schema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        StructuredTool.from_function(
            coroutine=move_email_to_folder_wrapper,
            name="move_email_to_folder",
            description="Move an email to a different folder (useful for marking as spam)",
            args_schema={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "IMAP message ID to move"
                    },
                    "source_folder": {
                        "type": "string",
                        "description": "source mailbox folder name",
                        "default": "INBOX"
                    },
                    "target_folder": {
                        "type": "string",
                        "description": "target mailbox folder name",
                        "default": "Junk"
                    }
                },
                "required": ["message_id"]
            }
        ),
        StructuredTool.from_function(
            coroutine=mark_email_as_read_wrapper,
            name="mark_email_as_read",
            description="Mark an email as read",
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
            coroutine=mark_email_as_unread_wrapper,
            name="mark_email_as_unread",
            description="Mark an email as unread",
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
            coroutine=delete_email_wrapper,
            name="delete_email",
            description="Delete an email",
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
            coroutine=get_server_capabilities_wrapper,
            name="get_server_capabilities",
            description="Get IMAP server capabilities and supported features",
            args_schema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
        StructuredTool.from_function(
            coroutine=send_email_wrapper,
            name="send_email",
            description="Send an email via SMTP",
            args_schema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "email subject"
                    },
                    "body": {
                        "type": "string",
                        "description": "email body content"
                    },
                    "cc": {
                        "type": "string",
                        "description": "CC recipients (comma-separated)",
                        "default": None
                    }
                },
                "required": ["to", "subject", "body"]
            }
        ),
        StructuredTool.from_function(
            coroutine=save_draft_wrapper,
            name="save_draft",
            description="Save an email as draft in the specified folder",
            args_schema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "email subject"
                    },
                    "body": {
                        "type": "string",
                        "description": "email body content"
                    },
                    "cc": {
                        "type": "string",
                        "description": "CC recipients (comma-separated)",
                        "default": None
                    },
                    "draft_folder": {
                        "type": "string",
                        "description": "folder to save draft in",
                        "default": "Drafts"
                    }
                },
                "required": ["to", "subject", "body"]
            }
        )
    ]

    # Draft saving is always available (uses IMAP only)
    tools.append(
        StructuredTool.from_function(
            coroutine=save_draft_wrapper,
            name="save_draft",
            description="Save an email as draft in the specified folder",
            args_schema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "string",
                        "description": "recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "email subject"
                    },
                    "body": {
                        "type": "string",
                        "description": "email body content"
                    },
                    "cc": {
                        "type": "string",
                        "description": "CC recipients (comma-separated)",
                        "default": None
                    },
                    "draft_folder": {
                        "type": "string",
                        "description": "folder to save draft in",
                        "default": "Drafts"
                    }
                },
                "required": ["to", "subject", "body"]
            }
        )
    )

    # Email sending only if enabled (requires SMTP configuration)
    if enable_sending:
        tools.append(
            StructuredTool.from_function(
                coroutine=send_email_wrapper,
                name="send_email",
                description="Send an email via SMTP",
                args_schema={
                    "type": "object",
                    "properties": {
                        "to": {
                            "type": "string",
                            "description": "recipient email address"
                        },
                        "subject": {
                            "type": "string",
                            "description": "email subject"
                        },
                        "body": {
                            "type": "string",
                            "description": "email body content"
                        },
                        "cc": {
                            "type": "string",
                            "description": "CC recipients (comma-separated)",
                            "default": None
                        }
                    },
                    "required": ["to", "subject", "body"]
                }
            )
        )

    return tools
