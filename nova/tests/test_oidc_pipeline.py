from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from social_core.exceptions import AuthForbidden

from nova.models.OIDCIdentity import OIDCIdentity, OIDCIdentityLinkAudit
from nova.oidc.pipeline import resolve_oidc_identity


class OIDCPipelineTests(TestCase):
    issuer = "https://auth.example.test/application/o/nova"

    def backend(self, **claims):
        return SimpleNamespace(id_token={"iss": self.issuer, "sub": "subject-1", "preferred_username": "alice", **claims})

    @override_settings(NOVA_OIDC_ISSUER=issuer, NOVA_OIDC_AUTO_PROVISION=True)
    def test_provisions_identity_without_admin_rights(self):
        result = resolve_oidc_identity(self.backend(), "subject-1", {})
        user = result["user"]
        self.assertEqual(user.username, "alice")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(OIDCIdentity.objects.get().user, user)
        self.assertEqual(OIDCIdentityLinkAudit.objects.get().method, "provisioned")

    @override_settings(NOVA_OIDC_ISSUER=issuer, NOVA_OIDC_LINK_EXISTING_USERS_BY_USERNAME=True)
    def test_links_existing_username_case_insensitively(self):
        user = get_user_model().objects.create_user(username="Alice", password="x")
        result = resolve_oidc_identity(self.backend(preferred_username="alice"), "subject-1", {})
        self.assertEqual(result["user"].pk, user.pk)
        self.assertEqual(OIDCIdentityLinkAudit.objects.get().method, "existing_username")

    @override_settings(NOVA_OIDC_ISSUER=issuer)
    def test_refuses_unprovisioned_identity(self):
        with self.assertRaises(AuthForbidden):
            resolve_oidc_identity(self.backend(), "subject-1", {})

    @override_settings(NOVA_OIDC_ISSUER=issuer, NOVA_OIDC_AUTO_PROVISION=True, NOVA_OIDC_REQUIRED_CLAIM="groups", NOVA_OIDC_REQUIRED_VALUES=["nova-users"])
    def test_refuses_user_outside_required_group(self):
        with self.assertRaises(AuthForbidden):
            resolve_oidc_identity(self.backend(groups=["other"]), "subject-1", {})
