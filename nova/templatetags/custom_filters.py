# nova/templatetags/custom_filters.py
from django import template

register = template.Library()

@register.filter
def get_item(value, key):
    """Safely get value[key] (dict.get) or value.key (attr)."""
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)
