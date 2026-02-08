# nova/llm/tool_error_handling.py
"""
Tool error handling middleware for Nova agents.
Provides centralized, consistent error handling for all tool calls.
"""
import asyncio
import logging
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import ToolMessage

logger = logging.getLogger(__name__)


class ToolErrorCategory:
    """Categories of tool errors for consistent handling."""
    NETWORK_ERROR = "network_error"
    VALIDATION_ERROR = "validation_error"
    RATE_LIMIT_ERROR = "rate_limit_error"
    AUTHENTICATION_ERROR = "authentication_error"
    API_ERROR = "api_error"
    TIMEOUT_ERROR = "timeout_error"
    UNKNOWN_ERROR = "unknown_error"


def categorize_error(error: Exception) -> str:
    """Categorize an exception for consistent handling."""
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()

    if 'timeout' in error_str or 'timeout' in error_type:
        return ToolErrorCategory.TIMEOUT_ERROR
    if any(keyword in error_str or keyword in error_type for keyword in
           ['connection', 'network', 'ssl', 'dns']):
        return ToolErrorCategory.NETWORK_ERROR
    elif any(keyword in error_str for keyword in
             ['validation', 'invalid', 'schema', 'required']):
        return ToolErrorCategory.VALIDATION_ERROR
    elif any(keyword in error_str for keyword in
             ['rate limit', 'too many requests', 'quota']):
        return ToolErrorCategory.RATE_LIMIT_ERROR
    elif any(keyword in error_str for keyword in
             ['auth', 'unauthorized', 'forbidden', 'credentials']):
        return ToolErrorCategory.AUTHENTICATION_ERROR
    elif any(keyword in error_str for keyword in
             ['api', 'http', 'status']):
        return ToolErrorCategory.API_ERROR
    else:
        return ToolErrorCategory.UNKNOWN_ERROR


def get_user_friendly_message(category: str, tool_name: str, original_error: Exception) -> str:
    """Convert technical errors to user-friendly messages."""
    messages = {
        ToolErrorCategory.NETWORK_ERROR: (
            f"Network connectivity issue while using {tool_name}. "
            "Please check your internet connection and try again."
        ),
        ToolErrorCategory.VALIDATION_ERROR: (
            f"Invalid input provided to {tool_name}. "
            "Please check your parameters and try again."
        ),
        ToolErrorCategory.RATE_LIMIT_ERROR: (
            f"{tool_name} is currently rate-limited. "
            "Please wait a moment before trying again."
        ),
        ToolErrorCategory.AUTHENTICATION_ERROR: (
            f"Authentication failed for {tool_name}. "
            "Please check your credentials and configuration."
        ),
        ToolErrorCategory.API_ERROR: (
            f"{tool_name} encountered an API error. "
            "The service may be temporarily unavailable."
        ),
        ToolErrorCategory.TIMEOUT_ERROR: (
            f"{tool_name} timed out. "
            "The operation took too long to complete."
        ),
        ToolErrorCategory.UNKNOWN_ERROR: (
            f"An unexpected error occurred with {tool_name}. "
            "Please try again or contact support if the issue persists."
        )
    }
    return messages.get(category, messages[ToolErrorCategory.UNKNOWN_ERROR])


async def retry_with_backoff(handler, request, max_retries: int = 3):
    """Retry a tool call with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return await handler(request)
        except Exception as e:
            category = categorize_error(e)

            # Only retry network and API errors, not validation errors
            if category not in [ToolErrorCategory.NETWORK_ERROR, ToolErrorCategory.API_ERROR]:
                raise

            if attempt == max_retries - 1:  # Last attempt
                raise

            # Exponential backoff: 1s, 2s, 4s
            delay = 2 ** attempt
            logger.warning(f"Tool call failed (attempt {attempt + 1}/{max_retries}), retrying in {delay}s: {e}")
            await asyncio.sleep(delay)

    # This should never be reached, but just in case
    raise RuntimeError("Retry logic failed unexpectedly")


@wrap_tool_call
async def handle_tool_errors(request, handler):
    """
    Centralized tool error handling middleware.

    Provides:
    - Consistent error categorization
    - User-friendly error messages
    - Automatic retry for transient failures
    - Comprehensive logging
    """
    tool_name = request.tool_call.get("name", "unknown_tool")

    try:
        # Attempt the tool call
        return await handler(request)

    except Exception as original_error:
        # Don't catch GraphInterrupt - it's a control flow mechanism, not an error
        from langgraph.errors import GraphInterrupt
        if isinstance(original_error, GraphInterrupt):
            raise

        category = categorize_error(original_error)
        user_message = get_user_friendly_message(category, tool_name, original_error)

        # Log the full error for debugging
        logger.error(
            f"Tool '{tool_name}' failed with {category}: {original_error}",
            exc_info=original_error,
            extra={
                "tool_name": tool_name,
                "error_category": category,
                "user_message": user_message
            }
        )

        # For certain error types, attempt retry
        if category in [ToolErrorCategory.NETWORK_ERROR, ToolErrorCategory.API_ERROR]:
            try:
                logger.info(f"Attempting retry for {tool_name} due to {category}")
                return await retry_with_backoff(handler, request)
            except Exception as retry_error:
                logger.error(f"Retry also failed for {tool_name}: {retry_error}")
                # Fall through to error response

        # Return user-friendly error message
        return ToolMessage(
            content=user_message,
            tool_call_id=request.tool_call.get("id"),
            additional_kwargs={
                "error_category": category,
                "original_error": str(original_error)
            }
        )
