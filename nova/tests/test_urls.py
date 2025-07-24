# nova/tests/test_urls.py
"""
Tests for URL resolution and routing.

Verifies that URL patterns correctly map to their corresponding views
and that authentication requirements are properly enforced.
"""

from django.test import TestCase, Client
from django.urls import reverse, resolve
from django.contrib.auth.models import User

from nova.views.main_views import (
    index, message_list, create_thread, delete_thread, 
    add_message, stream_llm_response
)
from nova.views.user_config_views import UserConfigView
from nova.views.provider_views import create_provider, edit_provider, delete_provider
from nova.views.agent_views import create_agent, edit_agent, delete_agent, make_default_agent
from nova.views.tools_views import create_tool, edit_tool, delete_tool, configure_tool, test_tool_connection
from nova.views.security_views import csrf_token
from nova.api.views import QuestionAnswerView
from nova.api.urls import APIRootView


class URLResolutionTests(TestCase):
    """Test that URLs resolve to the correct view functions/classes."""
    
    def setUp(self):
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client.force_login(self.user)

    # ------------------------------------------------------------------ #
    #  Main views                                                        #
    # ------------------------------------------------------------------ #
    def test_index_url_resolves(self):
        """Test that root URL resolves to index view."""
        url = reverse('index')
        resolver = resolve(url)
        self.assertEqual(resolver.func, index)

    def test_message_list_url_resolves(self):
        """Test that message-list URL resolves correctly."""
        url = reverse('message_list')
        resolver = resolve(url)
        self.assertEqual(resolver.func, message_list)

    def test_create_thread_url_resolves(self):
        """Test that create-thread URL resolves correctly."""
        url = reverse('create_thread')
        resolver = resolve(url)
        self.assertEqual(resolver.func, create_thread)

    def test_delete_thread_url_resolves(self):
        """Test that delete-thread URL resolves correctly."""
        url = reverse('delete_thread', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, delete_thread)

    def test_add_message_url_resolves(self):
        """Test that add-message URL resolves correctly."""
        url = reverse('add_message')
        resolver = resolve(url)
        self.assertEqual(resolver.func, add_message)

    def test_stream_llm_response_url_resolves(self):
        """Test that stream-llm-response URL resolves correctly."""
        url = reverse('stream_llm_response', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, stream_llm_response)

    # ------------------------------------------------------------------ #
    #  User config views                                                 #
    # ------------------------------------------------------------------ #
    def test_user_config_url_resolves(self):
        """Test that user-config URL resolves to UserConfigView."""
        url = reverse('user_config')
        resolver = resolve(url)
        self.assertEqual(resolver.func.view_class, UserConfigView)

    # ------------------------------------------------------------------ #
    #  Provider management views                                         #
    # ------------------------------------------------------------------ #
    def test_create_provider_url_resolves(self):
        """Test that create-provider URL resolves correctly."""
        url = reverse('create_provider')
        resolver = resolve(url)
        self.assertEqual(resolver.func, create_provider)

    def test_edit_provider_url_resolves(self):
        """Test that edit-provider URL resolves correctly."""
        url = reverse('edit_provider', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, edit_provider)

    def test_delete_provider_url_resolves(self):
        """Test that delete-provider URL resolves correctly."""
        url = reverse('delete_provider', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, delete_provider)

    # ------------------------------------------------------------------ #
    #  Agent management views                                            #
    # ------------------------------------------------------------------ #
    def test_create_agent_url_resolves(self):
        """Test that create-agent URL resolves correctly."""
        url = reverse('create_agent')
        resolver = resolve(url)
        self.assertEqual(resolver.func, create_agent)

    def test_edit_agent_url_resolves(self):
        """Test that edit-agent URL resolves correctly."""
        url = reverse('edit_agent', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, edit_agent)

    def test_delete_agent_url_resolves(self):
        """Test that delete-agent URL resolves correctly."""
        url = reverse('delete_agent', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, delete_agent)

    def test_make_default_agent_url_resolves(self):
        """Test that make-default-agent URL resolves correctly."""
        url = reverse('make_default_agent', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, make_default_agent)

    # ------------------------------------------------------------------ #
    #  Tool management views                                             #
    # ------------------------------------------------------------------ #
    def test_create_tool_url_resolves(self):
        """Test that create-tool URL resolves correctly."""
        url = reverse('create_tool')
        resolver = resolve(url)
        self.assertEqual(resolver.func, create_tool)

    def test_edit_tool_url_resolves(self):
        """Test that edit-tool URL resolves correctly."""
        url = reverse('edit_tool', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, edit_tool)

    def test_delete_tool_url_resolves(self):
        """Test that delete-tool URL resolves correctly."""
        url = reverse('delete_tool', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, delete_tool)

    def test_configure_tool_url_resolves(self):
        """Test that configure-tool URL resolves correctly."""
        url = reverse('configure_tool', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, configure_tool)

    def test_test_tool_connection_url_resolves(self):
        """Test that test-tool-connection URL resolves correctly."""
        url = reverse('test_tool_connection', args=[1])
        resolver = resolve(url)
        self.assertEqual(resolver.func, test_tool_connection)

    # ------------------------------------------------------------------ #
    #  API views                                                         #
    # ------------------------------------------------------------------ #
    def test_api_root_url_resolves(self):
        """Test that api-root URL resolves to APIRootView."""
        url = reverse('api-root')
        resolver = resolve(url)
        self.assertEqual(resolver.func.view_class, APIRootView)

    def test_ask_question_url_resolves(self):
        """Test that ask-question URL resolves to QuestionAnswerView."""
        url = reverse('ask-question')
        resolver = resolve(url)
        self.assertEqual(resolver.func.view_class, QuestionAnswerView)

    # ------------------------------------------------------------------ #
    #  Security views                                                    #
    # ------------------------------------------------------------------ #
    def test_csrf_token_url_resolves(self):
        """Test that api-csrf URL resolves correctly."""
        url = reverse('api-csrf')
        resolver = resolve(url)
        self.assertEqual(resolver.func, csrf_token)


class URLAccessTests(TestCase):
    """Test authentication requirements for URLs."""
    
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )

    def test_index_requires_authentication(self):
        """Test that index page requires authentication."""
        response = self.client.get(reverse('index'))
        self.assertRedirects(response, '/accounts/login/?next=/')

    def test_index_accessible_when_authenticated(self):
        """Test that index page is accessible when authenticated."""
        self.client.login(username='testuser', password='testpass123')
        response = self.client.get(reverse('index'))
        self.assertEqual(response.status_code, 200)

    def test_message_list_requires_authentication(self):
        """Test that message list requires authentication."""
        response = self.client.get(reverse('message_list'))
        self.assertRedirects(response, '/accounts/login/?next=/message-list/')

    def test_user_config_requires_authentication(self):
        """Test that user config requires authentication."""
        response = self.client.get(reverse('user_config'))
        self.assertRedirects(response, '/accounts/login/?next=/user-config/')

    def test_api_ask_requires_authentication(self):
        """Test that API ask endpoint requires authentication."""
        response = self.client.get(reverse('ask-question'))
        self.assertEqual(response.status_code, 403)  # API returns 403 instead of redirect

    def test_csrf_token_accessible_without_authentication(self):
        """Test that CSRF token endpoint is accessible without authentication."""
        response = self.client.get(reverse('api-csrf'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('csrfToken', response.json())


class URLPatternTests(TestCase):
    """Test URL patterns and parameter handling."""
    
    def test_thread_id_url_pattern(self):
        """Test that thread ID URLs accept integers."""
        url = reverse('delete_thread', args=[123])
        self.assertEqual(url, '/delete-thread/123/')
        
        url = reverse('stream_llm_response', args=[456])
        self.assertEqual(url, '/stream-llm-response/456/')

    def test_provider_id_url_pattern(self):
        """Test that provider ID URLs accept integers."""
        url = reverse('edit_provider', args=[789])
        self.assertEqual(url, '/provider/edit/789/')

    def test_agent_id_url_pattern(self):
        """Test that agent ID URLs accept integers."""
        url = reverse('edit_agent', args=[101])
        self.assertEqual(url, '/agent/edit/101/')

    def test_tool_id_url_pattern(self):
        """Test that tool ID URLs accept integers."""
        url = reverse('edit_tool', args=[202])
        self.assertEqual(url, '/tool/edit/202/')
        
        url = reverse('test_tool_connection', args=[303])
        self.assertEqual(url, '/tool/test-connection/303/')

    def test_api_urls_have_trailing_slash(self):
        """Test that API URLs have trailing slashes."""
        url = reverse('api-root')
        self.assertTrue(url.endswith('/'))
        
        url = reverse('ask-question')
        self.assertTrue(url.endswith('/'))
