from django.conf import settings


class StaticCacheControlMiddleware:
    """
    Middleware to add no-cache headers to static and media files in development.
    This ensures that when accessing static files directly through Django's runserver
    (e.g., on port 8000), they are not cached by the browser.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Only apply in DEBUG mode
        if settings.DEBUG:
            path = request.path_info
            # Check if it's a static or media file
            if path.startswith('/static/') or path.startswith('/media/'):
                response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response['Pragma'] = 'no-cache'
                response['Expires'] = '0'

        return response
