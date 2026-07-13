from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from social_core.exceptions import AuthForbidden

from nova.models.OIDCIdentity import OIDCIdentity, OIDCIdentityLinkAudit


def _deny(backend, message):
    raise AuthForbidden(backend, message)


def resolve_oidc_identity(backend, uid, details, *args, **kwargs):
    """Associate only a validated issuer/sub pair; never store OAuth data."""
    claims = backend.id_token or {}
    issuer = claims.get("iss", "").rstrip("/")
    subject = str(claims.get("sub", ""))
    username = claims.get("preferred_username")
    if not issuer or not subject or not username:
        _deny(backend, "OIDC identity is missing issuer, subject, or preferred_username")
    if issuer != settings.NOVA_OIDC_ISSUER:
        _deny(backend, "OIDC issuer does not match Nova configuration")

    claim_name = settings.NOVA_OIDC_REQUIRED_CLAIM
    allowed = set(settings.NOVA_OIDC_REQUIRED_VALUES)
    if claim_name and allowed:
        actual = claims.get(claim_name)
        actual_values = set(actual if isinstance(actual, list) else [actual])
        if not actual_values.intersection(allowed):
            _deny(backend, "OIDC account is not authorized for Nova")

    User = get_user_model()
    try:
        with transaction.atomic():
            identity = OIDCIdentity.objects.select_related("user").filter(issuer=issuer, subject=subject).first()
            if identity:
                if not identity.user.is_active:
                    _deny(backend, "Nova account is inactive")
                return {"user": identity.user}

            matches = list(User.objects.filter(username__iexact=username)[:2])
            if len(matches) > 1:
                _deny(backend, "Matching Nova username is ambiguous")
            if matches:
                if not settings.NOVA_OIDC_LINK_EXISTING_USERS_BY_USERNAME:
                    _deny(backend, "Matching local Nova account already exists")
                user = matches[0]
                method = OIDCIdentityLinkAudit.Method.EXISTING_USERNAME
            elif settings.NOVA_OIDC_AUTO_PROVISION:
                user = User(username=username)
                user.set_unusable_password()
                user.save()
                method = OIDCIdentityLinkAudit.Method.PROVISIONED
            else:
                _deny(backend, "Provisioning is disabled")
            if not user.is_active:
                _deny(backend, "Nova account is inactive")
            identity = OIDCIdentity.objects.create(issuer=issuer, subject=subject, user=user)
            OIDCIdentityLinkAudit.objects.create(identity=identity, user=user, method=method, preferred_username=username)
            return {"user": user}
    except IntegrityError:
        _deny(backend, "OIDC identity could not be linked safely")
