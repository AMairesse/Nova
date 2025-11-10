# Ensure Django loads split model modules that define models.
from .WebAppFile import WebAppFile  # noqa: F401
# Do NOT import WebApp here to avoid shadowing the submodule 'nova.models.WebApp',
# which migrations reference for default=uuid_hex.
