from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from ..models import LLMProvider

class ProviderViewsTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='testuser',
            password='testpass123'
        )
        self.client.force_login(self.user)
        
        # Create a test provider
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type="mistral",
            model="mistral-large-latest",
            api_key="test_key"
        )

    def test_create_provider(self):
        """Test creating a new provider"""
        provider_data = {
            'name': 'New Provider',
            'provider_type': 'openai',
            'model': 'gpt-4o',
            'api_key': 'new_test_key',
            'base_url': 'https://api.openai.com/v1'
        }
        
        response = self.client.post(reverse('create_provider'), provider_data)
        
        # Check redirect
        self.assertRedirects(response, reverse('user_config') + '?tab=providers')
        
        # Check provider was created
        self.assertTrue(LLMProvider.objects.filter(name='New Provider').exists())
        
        # Verify provider details
        provider = LLMProvider.objects.get(name='New Provider')
        self.assertEqual(provider.provider_type, 'openai')
        self.assertEqual(provider.model, 'gpt-4o')
        self.assertEqual(provider.api_key, 'new_test_key')
        self.assertEqual(provider.base_url, 'https://api.openai.com/v1')

    def test_edit_provider(self):
        """Test editing an existing provider"""
        edit_data = {
            'name': 'Updated Provider',
            'provider_type': 'ollama',
            'model': 'llama3',
            'base_url': 'http://localhost:11434'
        }
        
        response = self.client.post(
            reverse('edit_provider', args=[self.provider.id]), 
            edit_data
        )
        
        # Check redirect
        self.assertRedirects(response, reverse('user_config') + '?tab=providers')
        
        # Refresh provider from database
        self.provider.refresh_from_db()
        
        # Verify provider was updated
        self.assertEqual(self.provider.name, 'Updated Provider')
        self.assertEqual(self.provider.provider_type, 'ollama')
        self.assertEqual(self.provider.model, 'llama3')
        self.assertEqual(self.provider.base_url, 'http://localhost:11434')
        
        # API key should not have changed since we didn't provide a new one
        self.assertEqual(self.provider.api_key, 'test_key')

    def test_edit_provider_with_api_key(self):
        """Test editing a provider and updating the API key"""
        edit_data = {
            'name': 'Updated Provider',
            'provider_type': 'mistral',
            'model': 'mistral-small-latest',
            'api_key': 'new_api_key'
        }
        
        response = self.client.post(
            reverse('edit_provider', args=[self.provider.id]), 
            edit_data
        )
        
        # Refresh provider from database
        self.provider.refresh_from_db()
        
        # Verify API key was updated
        self.assertEqual(self.provider.api_key, 'new_api_key')

    def test_delete_provider(self):
        """Test deleting a provider"""
        response = self.client.post(reverse('delete_provider', args=[self.provider.id]))
        
        # Check redirect
        self.assertRedirects(response, reverse('user_config') + '?tab=providers')
        
        # Verify provider was deleted
        self.assertFalse(LLMProvider.objects.filter(id=self.provider.id).exists())

    def test_delete_provider_with_agents(self):
        """Test deleting a provider that has agents associated with it"""
        # Create an agent that uses the provider
        from ..models import Agent
        agent = Agent.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=self.provider,
            system_prompt="You are a test agent"
        )
        
        response = self.client.post(reverse('delete_provider', args=[self.provider.id]))
        
        # Check redirect
        self.assertRedirects(response, reverse('user_config') + '?tab=providers')
        
        # Verify provider was deleted
        self.assertFalse(LLMProvider.objects.filter(id=self.provider.id).exists())
        
        # Verify agent was also deleted
        self.assertFalse(Agent.objects.filter(id=agent.id).exists())

    def test_user_config_view_includes_providers(self):
        """Test that the user config view includes providers in the context"""
        response = self.client.get(reverse('user_config'))
        
        # Check that providers are in the context
        self.assertIn('llm_providers', response.context)
        
        # Check that our test provider is in the list
        providers = response.context['llm_providers']
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].name, 'Test Provider')
