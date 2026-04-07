from __future__ import annotations

import importlib
import logging
from typing import Any

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from nova.plugins import (
    get_builtin_plugin_metadata,
    get_internal_plugins,
    get_plugin_for_builtin_python_path,
)

logger = logging.getLogger(__name__)


def get_available_tool_types() -> dict[str, dict[str, Any]]:
    tool_types: dict[str, dict[str, Any]] = {}
    for plugin in get_internal_plugins():
        if not plugin.builtin_subtypes:
            continue
        metadata = plugin.build_builtin_metadata()
        for subtype in plugin.builtin_subtypes:
            tool_types[subtype] = dict(metadata)
    return tool_types


def get_tool_type(type_id: str) -> dict[str, Any] | None:
    return get_builtin_plugin_metadata(type_id)


def import_module(python_path: str) -> Any | None:
    plugin = get_plugin_for_builtin_python_path(python_path)
    resolved_path = plugin.python_path if plugin is not None else str(python_path or "").strip()
    if not resolved_path.startswith("nova.plugins."):
        raise ValidationError(_("Invalid python_path: Must resolve under 'nova.plugins.'"))

    try:
        return importlib.import_module(resolved_path)
    except ImportError as exc:
        logger.error(_("Could not import module: %s"), exc)
        return None


def get_metadata(python_path: str) -> dict[str, Any]:
    plugin = get_plugin_for_builtin_python_path(python_path)
    if plugin is None:
        return {}
    return plugin.build_builtin_metadata()
