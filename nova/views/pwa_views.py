# nova/views/pwa_views.py
from django.http import HttpResponse, Http404
from django.contrib.staticfiles import finders
import os


def service_worker(request):
    """
    Serve the service worker from the staticfiles storage at the root URL (/sw.js).
    """
    path = finders.find('sw.js')
    if not path or not os.path.exists(path):
        raise Http404('Service worker not found')
    with open(path, 'rb') as f:
        content = f.read()
    resp = HttpResponse(content, content_type='application/javascript')
    # Not strictly required when served at /sw.js, but harmless:
    resp['Service-Worker-Allowed'] = '/'
    return resp
