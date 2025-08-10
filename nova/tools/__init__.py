# nova/tools/__init__.py
import os
import importlib
import logging
from typing import Any, Dict, Optional
from django.utils.translation import gettext_lazy as _
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

def get_available_tool_types() -> Dict[str, Dict[str, Any]]:
    """
    Discover all builtin tools by scanning the builtins/ directory.
    Returns dict: subtype → metadata (from each file's METADATA).
    """
    tool_types = {}
    base_dir = os.path.dirname(os.path.abspath(__file__))
    builtins_dir = os.path.join(base_dir, 'builtins')
    
    if not os.path.exists(builtins_dir):
        logger.warning("Builtins directory not found: %s", builtins_dir)
        return tool_types
    
    for filename in os.listdir(builtins_dir):
        if not filename.endswith('.py') or filename.startswith('_'):
            continue
            
        module_name = f"nova.tools.builtins.{filename[:-3]}"
        try:
            module = importlib.import_module(module_name)
            if hasattr(module, 'METADATA'):
                subtype = filename[:-3]
                tool_types[subtype] = module.METADATA
                tool_types[subtype]["python_path"] = module_name
            else:
                logger.warning("No METADATA in %s", module_name)
        except ImportError as e:
            logger.error("Failed to import %s: %s", module_name, e)
            
    return tool_types

def get_tool_type(type_id: str) -> Optional[Dict[str, Any]]:
    """
    Return the metadata dict for a single tool type, or None if not found.
    """
    return get_available_tool_types().get(type_id)

def import_module(python_path: str) -> Optional[Any]:
    """
    Safely import a module with whitelisting.
    Only allow paths under 'nova.tools.builtins.'.
    """
    if not python_path.startswith('nova.tools.builtins.'):
        raise ValidationError(_("Invalid python_path: Must be under 'nova.tools.builtins.'."))
    
    try:
        module = importlib.import_module(python_path)
        return module
    except ImportError as e:
        logger.error(_("Could not import module: %s"), e)
        return None

def get_metadata(python_path: str) -> Dict[str, Any]:
    """
    Returns METADATA for the given python_path.
    Validates path first.
    """
    module = import_module(python_path)
    if module and hasattr(module, 'METADATA'):
        return module.METADATA
    return {}
