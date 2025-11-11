from django.conf import settings


def compute_webapp_public_url(slug: str) -> str:
    """
    Compute a robust public URL for a WebApp.

    Preference order:
    1. First CSRF_TRUSTED_ORIGIN (if set), e.g. "https://example.com"
    2. Single ALLOWED_HOSTS entry (non-wildcard), with scheme:
       - if value already contains scheme, keep it
       - else:
         - https when SECURE_SSL_REDIRECT or not DEBUG
         - http otherwise
    3. Fallback to relative path: /apps/<slug>/

    Always returns a URL ending with "/apps/<slug>/".
    """
    base = None

    # 1) CSRF_TRUSTED_ORIGINS
    origins = getattr(settings, "CSRF_TRUSTED_ORIGINS", None) or []
    if origins:
        base = origins[0].rstrip("/")

    # 2) ALLOWED_HOSTS heuristic (single non-wildcard host)
    if not base:
        hosts = [h for h in getattr(settings, "ALLOWED_HOSTS", []) if h and "*" not in h]
        if len(hosts) == 1:
            host = hosts[0]
            if host.startswith("http://") or host.startswith("https://"):
                base = host.rstrip("/")
            else:
                use_https = getattr(settings, "SECURE_SSL_REDIRECT", False) or not getattr(settings, "DEBUG", False)
                scheme = "https" if use_https else "http"
                base = f"{scheme}://{host}".rstrip("/")

    # 3) Fallback: relative URL only
    if not base:
        return f"/apps/{slug}/"

    return f"{base}/apps/{slug}/"
