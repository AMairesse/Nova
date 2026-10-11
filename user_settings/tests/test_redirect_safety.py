from urllib.parse import urlsplit

from django.http import HttpResponseRedirect
from django.test import RequestFactory, SimpleTestCase
from django.urls import reverse

from user_settings.views.agent import AgentCreateView
from user_settings.views.api_token import DeleteAPITokenView, GenerateAPITokenView


class DashboardRedirectSafetyTests(SimpleTestCase):
    def test_untrusted_from_only_changes_the_fragment(self):
        factory = RequestFactory()
        for view_class in (AgentCreateView, GenerateAPITokenView, DeleteAPITokenView):
            for value in ("general", "https://evil.invalid/path", "//evil.invalid/path", "../other?next=https://evil.invalid"):
                for method in ("get", "post"):
                    with self.subTest(view=view_class.__name__, value=value, method=method):
                        view = view_class()
                        view.request = getattr(factory, method)("/settings/", {"from": value})
                        response = HttpResponseRedirect(view.get_success_url())
                        target = urlsplit(response["Location"])
                        self.assertEqual((target.scheme, target.netloc, target.path, target.query),
                                         ("", "", reverse("user_settings:dashboard"), ""))
                        self.assertTrue(target.fragment.startswith("pane-"))
