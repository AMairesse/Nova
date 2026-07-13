from django.conf import settings
from django.db import models


class OIDCIdentity(models.Model):
    issuer = models.URLField(max_length=500)
    subject = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="oidc_identities")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("issuer", "subject"), name="nova_oidc_issuer_subject_unique")]


class OIDCIdentityLinkAudit(models.Model):
    class Method(models.TextChoices):
        EXISTING_USERNAME = "existing_username", "Existing username"
        PROVISIONED = "provisioned", "Provisioned"

    identity = models.ForeignKey(OIDCIdentity, on_delete=models.PROTECT, related_name="link_audits")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    method = models.CharField(max_length=32, choices=Method.choices)
    preferred_username = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

