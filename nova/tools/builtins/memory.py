import re
from django.core.exceptions import ValidationError
from langchain_core.tools import StructuredTool
from nova.llm.llm_agent import LLMAgent
from nova.utils import get_theme_content
from asgiref.sync import sync_to_async

METADATA = {
    'name': 'Memory',
    'description': 'Access and manage user information stored in Markdown format',
    'requires_config': False,
    'config_fields': [],
    'test_function': None,
    'test_function_args': [],
}


def _get_user_info(user):
    """Sync function to get or create UserInfo."""
    from nova.models.UserObjects import UserInfo
    user_info, _ = UserInfo.objects.get_or_create(user=user)
    return user_info


def _update_user_info(user, content):
    """Sync function to update UserInfo."""
    from nova.models.UserObjects import UserInfo
    user_info, _ = UserInfo.objects.get_or_create(user=user)
    user_info.markdown_content = content
    user_info.full_clean()  # Validate before saving
    user_info.save()
    return user_info


def _get_theme_content(content: str, theme: str) -> str:
    """Get content for a specific theme."""
    return get_theme_content(content, theme)


def _set_theme_content(content: str, theme: str, new_content: str) -> str:
    """Update or add content for a specific theme."""
    lines = content.split('\n')
    new_lines = []
    in_theme = False
    theme_replaced = False

    for line in lines:
        if line.strip().startswith('# ') and line.strip()[2:].strip() == theme:
            in_theme = True
            theme_replaced = True
            new_lines.append(line)
            # Add the new content
            if new_content.strip():
                new_lines.extend(new_content.strip().split('\n'))
        elif line.strip().startswith('# ') and in_theme:
            in_theme = False
            new_lines.append(line)
        elif not in_theme:
            new_lines.append(line)

    # If theme not found, add it at the end
    if not theme_replaced:
        if content.strip():
            new_lines.append('')
        new_lines.append(f'# {theme}')
        if new_content.strip():
            new_lines.extend(new_content.strip().split('\n'))

    return '\n'.join(new_lines).strip()


def _delete_theme_content(content: str, theme: str) -> str:
    """Remove a specific theme."""
    lines = content.split('\n')
    new_lines = []
    in_theme = False

    for line in lines:
        if line.strip().startswith('# ') and line.strip()[2:].strip() == theme:
            in_theme = True
        elif line.strip().startswith('# ') and in_theme:
            in_theme = False
        elif not in_theme:
            new_lines.append(line)

    return '\n'.join(new_lines).strip()


async def get_info(theme: str, agent: LLMAgent) -> str:
    """Get information for a specific theme."""
    try:
        user_info = await sync_to_async(_get_user_info)(agent.user)
        content = user_info.markdown_content

        if not content:
            return f"No information stored for theme '{theme}'."

        theme_content = _get_theme_content(content, theme)
        if not theme_content:
            return f"No information stored for theme '{theme}'."

        return theme_content
    except Exception as e:
        return f"Error retrieving information for theme '{theme}': {str(e)}"


async def set_info(theme: str, content: str, agent: LLMAgent) -> str:
    """Set or update information for a specific theme."""
    try:
        # Validate content doesn't contain malicious patterns
        if re.search(r'<script|<iframe|<object', content, re.IGNORECASE):
            raise ValidationError("Content contains potentially unsafe HTML tags.")

        user_info = await sync_to_async(_get_user_info)(agent.user)
        current_content = user_info.markdown_content

        updated_content = _set_theme_content(current_content, theme, content)
        await sync_to_async(_update_user_info)(agent.user, updated_content)

        return f"Information for theme '{theme}' has been updated."
    except Exception as e:
        return f"Error updating information for theme '{theme}': {str(e)}"


async def delete_theme(theme: str, agent: LLMAgent) -> str:
    """Delete a specific theme."""
    if theme == "global_user_preferences":
        return "The 'global_user_preferences' theme cannot be deleted as it is required."

    try:
        user_info = await sync_to_async(_get_user_info)(agent.user)
        current_content = user_info.markdown_content

        updated_content = _delete_theme_content(current_content, theme)
        await sync_to_async(_update_user_info)(agent.user, updated_content)

        return f"Theme '{theme}' has been deleted."
    except Exception as e:
        return f"Error deleting theme '{theme}': {str(e)}"


async def create_theme(theme: str, agent: LLMAgent) -> str:
    """Create a new theme with empty content."""
    try:
        user_info = await sync_to_async(_get_user_info)(agent.user)
        current_content = user_info.markdown_content

        # Check if theme already exists
        themes = await sync_to_async(user_info.get_themes)()
        if theme in themes:
            return f"Theme '{theme}' already exists."

        updated_content = _set_theme_content(current_content, theme, "")
        await sync_to_async(_update_user_info)(agent.user, updated_content)

        return f"Theme '{theme}' has been created."
    except Exception as e:
        return f"Error creating theme '{theme}': {str(e)}"


async def list_themes(agent: LLMAgent) -> str:
    """List all available themes."""
    try:
        user_info = await sync_to_async(_get_user_info)(agent.user)
        content = user_info.markdown_content

        if not content:
            return "No themes available."

        themes = await sync_to_async(user_info.get_themes)()
        if not themes:
            return "No themes available."

        return "Available themes:\n" + "\n".join(f"- {theme}" for theme in themes)
    except Exception as e:
        return f"Error listing themes: {str(e)}"


async def get_functions(tool, agent: LLMAgent):
    """
    Return a list of StructuredTool instances for the available functions.
    """
    return [
        StructuredTool.from_function(
            coroutine=lambda theme: get_info(theme, agent),
            name="get_info",
            description="Retrieve stored information for a specific theme",
            args_schema={
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "description": "The theme name to retrieve information for"
                    }
                },
                "required": ["theme"]
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda theme, content: set_info(theme, content, agent),
            name="set_info",
            description="Store or update information for a specific theme",
            args_schema={
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "description": "The theme name to store information under"
                    },
                    "content": {
                        "type": "string",
                        "description": "The Markdown content to store"
                    }
                },
                "required": ["theme", "content"]
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda theme: delete_theme(theme, agent),
            name="delete_theme",
            description="Delete a specific theme",
            args_schema={
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "description": "The theme name to delete"
                    }
                },
                "required": ["theme"]
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda theme: create_theme(theme, agent),
            name="create_theme",
            description="Create a new theme for storing information",
            args_schema={
                "type": "object",
                "properties": {
                    "theme": {
                        "type": "string",
                        "description": "The name of the new theme to create"
                    }
                },
                "required": ["theme"]
            }
        ),
        StructuredTool.from_function(
            coroutine=lambda: list_themes(agent),
            name="list_themes",
            description="List all available themes",
            args_schema={
                "type": "object",
                "properties": {},
                "required": []
            }
        ),
    ]
