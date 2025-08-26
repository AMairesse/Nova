# nova/tests/test_forms.py
from django.core.exceptions import ValidationError
from django import forms as django_forms
from django.db import models as django_models
from unittest.mock import patch

from user_settings.forms import (
    UserParametersForm, UserProfileForm, LLMProviderForm,
    AgentForm, ToolForm, ToolCredentialForm
)
from nova.models.models import (
    LLMProvider, UserParameters, UserProfile, Agent, Tool, ProviderType
)
from .base import BaseTestCase


class UserParametersFormTest(BaseTestCase):
    def test_valid_form(self):
        existing_params = UserParameters.objects.get(user=self.user)
        data = {
            'allow_langfuse': True,
            'langfuse_public_key': 'pk-test',
            'langfuse_secret_key': 'sk-test',
            'langfuse_host': 'https://langfuse.example.com'
        }
        form = UserParametersForm(data=data, instance=existing_params)
        self.assertTrue(form.is_valid())
        params = form.save()
        self.assertTrue(params.allow_langfuse)

    def test_password_widgets(self):
        form = UserParametersForm()
        self.assertIsInstance(form.fields['langfuse_public_key'].widget,
                              django_forms.TextInput)
        self.assertIsInstance(form.fields['langfuse_secret_key'].widget,
                              django_forms.PasswordInput)


class UserProfileFormTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.agent = Agent.objects.create(
            user=self.user,
            name='Test Agent',
            llm_provider=LLMProvider.objects.create(
                user=self.user, name='Provider',
                provider_type=ProviderType.OLLAMA, model='llama3'
            ),
            system_prompt='Prompt'
        )

    def test_valid_form(self):
        # Utilise l'instance existante (auto-créée par signal) pour mise à jour
        existing_profile = UserProfile.objects.get(user=self.user)
        data = {'default_agent': self.agent.id}
        form = UserProfileForm(user=self.user, data=data,
                               instance=existing_profile)
        self.assertTrue(form.is_valid())
        profile = form.save()
        self.assertEqual(profile.default_agent, self.agent)


class LLMProviderFormTest(BaseTestCase):
    def test_valid_form(self):
        data = {
            'name': 'Test Provider',
            'provider_type': ProviderType.OLLAMA,
            'model': 'llama3',
            'api_key': 'fake_key',
            'max_context_tokens': 4096,
            'base_url': 'http://localhost:11434',
            'additional_config': '{}'
        }
        form = LLMProviderForm(data=data)
        self.assertTrue(form.is_valid())
        provider = form.save(commit=False)
        provider.user = self.user
        provider.save()
        self.assertEqual(provider.name, 'Test Provider')

    def test_clean_api_key_preserve_existing(self):
        existing = LLMProvider.objects.create(
            user=self.user, name='Existing', provider_type=ProviderType.OLLAMA,
            model='llama3', api_key='old_key', max_context_tokens=4096
        )
        data = {'name': 'Existing', 'provider_type': ProviderType.OLLAMA,
                'model': 'llama3', 'api_key': '', 'max_context_tokens': 4096}
        form = LLMProviderForm(data=data, instance=existing)
        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data['api_key'], 'old_key')

    def test_invalid_unique_name(self):
        LLMProvider.objects.create(user=self.user, name='Existing',
                                   provider_type=ProviderType.OLLAMA,
                                   model='llama3')
        data = {'name': 'Existing', 'provider_type': ProviderType.OPENAI,
                'model': 'gpt-4', 'max_context_tokens': 4096}
        form = LLMProviderForm(data=data)
        self.assertTrue(form.is_valid())
        provider = form.save(commit=False)
        provider.user = self.user
        with self.assertRaises(ValidationError):
            provider.full_clean()


class AgentFormTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.provider = LLMProvider.objects.create(
            user=self.user, name='Provider', provider_type=ProviderType.OLLAMA,
            model='llama3'
        )
        self.tool = Tool.objects.create(
            user=self.user, name='Test Tool', description='Test',
            tool_type=Tool.ToolType.API, endpoint='https://api.example.com'
        )

    def test_init_restricts_choices(self):
        form = AgentForm(user=self.user)
        self.assertQuerySetEqual(form.fields['llm_provider'].queryset,
                                 LLMProvider.objects.filter(user=self.user))
        self.assertQuerySetEqual(
            form.fields['tools'].queryset,
            Tool.objects.filter(
                django_models.Q(user=self.user) |
                django_models.Q(user__isnull=True),
                is_active=True
            )
        )
        self.assertQuerySetEqual(form.fields['agent_tools'].queryset,
                                 Agent.objects.filter(user=self.user,
                                 is_tool=True))

    def test_valid_agent(self):
        data = {
            'name': 'Test Agent',
            'llm_provider': self.provider.id,
            'system_prompt': 'You are helpful.',
            'recursion_limit': '25',
            'is_tool': False,
            'tools': [self.tool.id]
        }
        form = AgentForm(data=data, user=self.user)
        self.assertTrue(form.is_valid())
        agent = form.save(commit=False)
        agent.user = self.user
        agent.save()
        self.assertEqual(agent.name, 'Test Agent')

    def test_is_tool_requires_description(self):
        data = {
            'name': 'Tool Agent',
            'llm_provider': self.provider.id,
            'system_prompt': 'Tool prompt',
            'recursion_limit': '25',
            'is_tool': True
        }
        form = AgentForm(data=data, user=self.user)
        self.assertFalse(form.is_valid())
        self.assertIn('tool_description', form.errors)

    def test_clean_agent_tools_prevent_self_reference(self):
        agent = Agent.objects.create(user=self.user, name='A1',
                                     llm_provider=self.provider,
                                     system_prompt='P1', is_tool=True,
                                     tool_description='D1')
        data = {
            'name': 'A1',
            'llm_provider': self.provider.id,
            'system_prompt': 'P1',
            'recursion_limit': '25',
            'is_tool': True,
            'tool_description': 'D1',
            'agent_tools': []
        }
        form = AgentForm(data=data, instance=agent, user=self.user)
        self.assertTrue(form.is_valid())
        form.save()
        self.assertFalse(agent.agent_tools.filter(pk=agent.pk).exists())

    def test_cycle_detection_via_model(self):
        agent1 = Agent.objects.create(user=self.user, name='A1',
                                      llm_provider=self.provider,
                                      system_prompt='P1', is_tool=True,
                                      tool_description='D1')
        agent2 = Agent.objects.create(user=self.user, name='A2',
                                      llm_provider=self.provider,
                                      system_prompt='P2', is_tool=True,
                                      tool_description='D2')
        agent1.agent_tools.add(agent2)
        data = {
            'name': 'A2',
            'llm_provider': self.provider.id,
            'system_prompt': 'P2',
            'recursion_limit': '25',
            'is_tool': True,
            'tool_description': 'D2',
            'agent_tools': [agent1.id]
        }
        form = AgentForm(data=data, instance=agent2, user=self.user)
        self.assertTrue(form.is_valid())
        agent = form.save(commit=False)
        agent.user = self.user
        agent.agent_tools.set(form.cleaned_data['agent_tools'])
        with self.assertRaises(ValidationError):
            agent.full_clean()


class ToolFormTest(BaseTestCase):
    @patch('nova.tools.get_available_tool_types')
    @patch('nova.tools.get_tool_type')
    def test_init_populates_subtype_choices(self, mock_get_tool_type,
                                            mock_get_available_tool_types):
        mock_get_available_tool_types.return_value = {'date': {'name':
                                                               'Date Tool'}}
        form = ToolForm()
        self.assertIn(('date', 'Date Tool'),
                      form.fields['tool_subtype'].choices)

    @patch('nova.tools.get_tool_type')
    def test_clean_builtin_valid(self, mock_get_tool_type):
        mock_get_tool_type.return_value = {
            'name': 'Date Tool',
            'description': 'Date operations',
            'python_path': 'nova.tools.builtins.date',
            'input_schema': {'type': 'object'},
            'output_schema': {'type': 'object'}
        }
        data = {
            'tool_type': Tool.ToolType.BUILTIN,
            'tool_subtype': 'date',
            'is_active': True
        }
        form = ToolForm(data=data)
        self.assertTrue(form.is_valid())
        tool = form.save(commit=False)
        tool.user = self.user
        tool.save()
        self.assertEqual(tool.name, 'Date Tool')
        self.assertEqual(tool.python_path, 'nova.tools.builtins.date')

    def test_clean_builtin_missing_subtype(self):
        data = {'tool_type': Tool.ToolType.BUILTIN}
        form = ToolForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('__all__', form.errors)

    def test_clean_api_requires_fields(self):
        data = {'tool_type': Tool.ToolType.API}
        form = ToolForm(data=data)
        self.assertFalse(form.is_valid())
        self.assertIn('name', form.errors)
        self.assertIn('description', form.errors)
        self.assertIn('endpoint', form.errors)

    def test_save_sets_python_path_for_builtin(self):
        data = {
            'tool_type': Tool.ToolType.BUILTIN,
            'tool_subtype': 'date'
        }
        form = ToolForm(data=data)
        form.is_valid()
        tool = form.save(commit=False)
        self.assertTrue(tool.python_path.startswith('nova.tools.builtins.'))


class ToolCredentialFormTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.tool = Tool.objects.create(
            user=self.user, name='CalDav Tool', description='Test',
            tool_type=Tool.ToolType.API, endpoint='https://api.example.com'
        )

    def test_init_hides_fields_based_on_auth_type(self):
        form = ToolCredentialForm(initial={'auth_type': 'none'})
        self.assertIsInstance(form.fields['username'].widget,
                              django_forms.HiddenInput)
        self.assertIsInstance(form.fields['password'].widget,
                              django_forms.HiddenInput)

    def test_init_tool_specific_fields(self):
        form = ToolCredentialForm(tool=self.tool)
        self.assertTrue(form.fields['caldav_url'].required)

    def test_save_with_config(self):
        data = {
            'auth_type': 'basic',
            'username': 'test',
            'password': 'secret',
            'caldav_url': 'https://caldav.example.com'
        }
        form = ToolCredentialForm(data=data, tool=self.tool)
        self.assertTrue(form.is_valid())
        cred = form.save(commit=False)
        cred.user = self.user
        cred.tool = self.tool
        cred.save()
        self.assertEqual(cred.config.get('caldav_url'),
                         'https://caldav.example.com')
