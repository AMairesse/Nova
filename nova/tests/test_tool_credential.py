# nova/tests/test_tool_credential.py
from django.contrib.auth import get_user_model
from django.test import TestCase

from nova.models import Tool, ToolCredential

User = get_user_model()


class ToolCredentialTests(TestCase):
    def setUp(self) -> None:
        self.user = User.objects.create_user("alice", password="pwd")

        # Minimal builtin tool (CalDav)
        self.tool = Tool.objects.create(
            user=self.user,
            name="CalDav",
            description="CalDav helper",
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype="caldav",
            python_path="nova.tools.builtins.caldav",
            is_active=True,
        )

    # ------------------------------------------------------------------ #
    #  BASIC AUTH                                                        #
    # ------------------------------------------------------------------ #
    def test_create_basic_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="basic",
            username="alice",
            password="secret",
            config={"api_url": "https://example.com"},
        )
        self.assertEqual(str(cred), "alice's credentials for CalDav")
        self.assertEqual(cred.auth_type, "basic")
        self.assertEqual(cred.username, "alice")
        self.assertEqual(cred.password, "secret")
        self.assertEqual(cred.config, {"api_url": "https://example.com"})

    # ------------------------------------------------------------------ #
    #  TOKEN / API-KEY AUTH                                              #
    # ------------------------------------------------------------------ #
    def test_create_token_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="token",
            token="tok_123",
            token_type="Bearer",
        )
        self.assertEqual(cred.auth_type, "token")
        self.assertEqual(cred.token, "tok_123")
        self.assertIsNone(cred.username)
        self.assertIsNone(cred.password)

    def test_create_api_key_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="api_key",
            token="key_abc",
        )
        self.assertEqual(cred.auth_type, "api_key")
        self.assertEqual(cred.token, "key_abc")

    # ------------------------------------------------------------------ #
    #  OAUTH                                                             #
    # ------------------------------------------------------------------ #
    def test_create_oauth_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="oauth",
            client_id="cid",
            client_secret="csec",
            refresh_token="r123",
        )
        self.assertEqual(cred.auth_type, "oauth")
        self.assertEqual(cred.client_id, "cid")
        self.assertEqual(cred.client_secret, "csec")
        self.assertEqual(cred.refresh_token, "r123")

    # ------------------------------------------------------------------ #
    #  NO AUTH (“none”)                                                  #
    # ------------------------------------------------------------------ #
    def test_create_none_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="none",
        )
        self.assertEqual(cred.auth_type, "none")
        self.assertIsNone(cred.username)
        self.assertIsNone(cred.password)
        self.assertIsNone(cred.token)

    # ------------------------------------------------------------------ #
    #  UPDATE / DELETE FLOW                                              #
    # ------------------------------------------------------------------ #
    def test_update_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="basic",
            username="alice",
            password="secret",
        )
        cred.username = "bob"
        cred.password = "newpass"
        cred.config = {"foo": "bar"}
        cred.save()

        cred.refresh_from_db()
        self.assertEqual(cred.username, "bob")
        self.assertEqual(cred.password, "newpass")
        self.assertEqual(cred.config, {"foo": "bar"})

    def test_delete_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type="basic",
            username="alice",
            password="secret",
        )
        pk = cred.pk
        cred.delete()
        self.assertFalse(ToolCredential.objects.filter(pk=pk).exists())

    # ------------------------------------------------------------------ #
    #  UNIQUE (user, tool)                                               #
    # ------------------------------------------------------------------ #
    def test_unique_user_tool_constraint(self):
        ToolCredential.objects.create(
            user=self.user, tool=self.tool, auth_type="none"
        )
        with self.assertRaises(Exception):
            # Same (user, tool) pair must fail (IntegrityError wrapped by Django)
            ToolCredential.objects.create(
                user=self.user, tool=self.tool, auth_type="basic"
            )
