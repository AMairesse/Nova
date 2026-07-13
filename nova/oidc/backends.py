from asgiref.sync import sync_to_async
from social_core.backends.open_id_connect import OpenIdConnectAuth


class AsyncOpenIdConnectAuth(OpenIdConnectAuth):
    """Compatibility bridge for Django's asynchronous auth middleware."""

    async def aget_user(self, user_id):
        return await sync_to_async(self.get_user, thread_sensitive=True)(user_id)
