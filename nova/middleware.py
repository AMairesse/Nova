import ipaddress
from django.conf import settings
from django.http import HttpResponseForbidden


class AdminIPRestrictionMiddleware:
    """
    Middleware to restrict access to admin URLs based on IP addresses or CIDR ranges.
    Only allows access if the client's IP is in ALLOWED_ADMIN_IPS.
    Supports individual IPs (e.g., 192.168.1.1) and CIDR ranges (e.g., 192.168.1.0/24).
    If ALLOWED_ADMIN_IPS is empty, allows all access.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/supernova-admin/'):
            allowed_ips = getattr(settings, 'ALLOWED_ADMIN_IPS', [])
            if allowed_ips:
                client_ip = self.get_client_ip(request)
                if not self.is_ip_allowed(client_ip, allowed_ips):
                    return HttpResponseForbidden("Access denied: IP not allowed for admin access.")
        return self.get_response(request)

    def get_client_ip(self, request):
        x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
        if x_forwarded_for:
            ip = x_forwarded_for.split(',')[0]
        else:
            ip = request.META.get('REMOTE_ADDR')
        return ip

    def is_ip_allowed(self, client_ip, allowed_list):
        try:
            client_addr = ipaddress.ip_address(client_ip)
            for allowed in allowed_list:
                if '/' in allowed:
                    # CIDR range
                    network = ipaddress.ip_network(allowed, strict=False)
                    if client_addr in network:
                        return True
                else:
                    # Exact IP
                    if client_addr == ipaddress.ip_address(allowed):
                        return True
            return False
        except ValueError:
            # Invalid IP, deny access
            return False


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
