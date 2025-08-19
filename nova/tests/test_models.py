# nova/tests/test_models.py
from django.test import TestCase
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.utils import timezone
from unittest.mock import patch
import json

from nova.models.models import (
    LLMProvider, UserParameters, Tool, Agent, UserProfile,
    ToolCredential, Task, ProviderType, TaskStatus, UserFile
)
from nova.models.Message import Message, Actor
from nova.models.Thread import Thread
from .base import BaseTestCase  # Import the base test case

class LLMProviderModelTest(BaseTestCase):
    def test_create_provider(self):
        provider = LLMProvider.objects.create(
            user=self.user,
            name='Test Provider',
            provider_type=ProviderType.OLLAMA,
            model='llama3',
            api_key='fake_key',
            base_url='http://localhost:11434'
        )
        self.assertEqual(provider.name, 'Test Provider')
        self.assertEqual(provider.provider_type, ProviderType.OLLAMA)
        self.assertEqual(str(provider), 'Test Provider (ollama)')
    
    def test_unique_together(self):
        LLMProvider.objects.create(user=self.user, name='Unique', provider_type=ProviderType.OLLAMA, model='llama3')
        with self.assertRaises(ValidationError):
            duplicate = LLMProvider(user=self.user, name='Unique', provider_type=ProviderType.OPENAI, model='gpt-4')
            duplicate.full_clean()  # Trigger unique_together validation

class UserParametersModelTest(BaseTestCase):
    def test_create_parameters(self):
        # Vérifie l'auto-création via signal pour l'utilisateur de base
        auto_params = UserParameters.objects.get(user=self.user)
        self.assertFalse(auto_params.allow_langfuse)  # Default value
        self.assertEqual(str(auto_params), f'Parameters for {self.user.username}')
        
        # Crée un nouvel utilisateur (déclenche le signal pour auto-création)
        new_user = User.objects.create_user(username='newuser', email='new@example.com', password='newpass')
        
        # Vérifie l'auto-création pour le nouvel utilisateur
        new_auto_params = UserParameters.objects.get(user=new_user)
        self.assertFalse(new_auto_params.allow_langfuse)  # Default value
        
        # Teste la mise à jour (simule un scénario de "création" de valeurs sans violer unique)
        new_auto_params.allow_langfuse = True
        new_auto_params.save()
        updated_params = UserParameters.objects.get(user=new_user)
        self.assertTrue(updated_params.allow_langfuse)
        self.assertEqual(str(updated_params), f'Parameters for {new_user.username}')

class MessageModelTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject='Test Thread')
    
    def test_create_message(self):
        message = Message.objects.create(
            user=self.user,
            text='Hello',
            actor=Actor.USER,
            thread=self.thread
        )
        self.assertEqual(message.text, 'Hello')
        self.assertEqual(str(message), 'Hello')

class ToolModelTest(BaseTestCase):
    @patch('nova.tools.get_tool_type')  # Patch au bon endroit (source de la fonction)
    def test_clean_builtin_tool_valid(self, mock_get_tool_type):
        mock_get_tool_type.return_value = {
            'python_path': 'nova.tools.builtins.date',
            'input_schema': {'type': 'object'},
            'output_schema': {'type': 'object'}
        }
        tool = Tool(
            user=self.user,
            name='Date Tool',
            description='Date operations',
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype='date'
        )
        tool.full_clean()  # Should pass with mocked metadata
        self.assertEqual(tool.python_path, 'nova.tools.builtins.date')
    
    def test_clean_builtin_tool_invalid_subtype(self):
        tool = Tool(
            user=self.user,
            name='Invalid Builtin',
            description='Test',
            tool_type=Tool.ToolType.BUILTIN,
            tool_subtype='invalid'
        )
        with self.assertRaises(ValidationError):
            tool.full_clean()
    
    def test_clean_api_tool_requires_endpoint(self):
        tool = Tool(
            user=self.user,
            name='API Tool',
            description='Test API',
            tool_type=Tool.ToolType.API
        )
        with self.assertRaises(ValidationError):
            tool.full_clean()

class AgentModelTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.provider = LLMProvider.objects.create(
            user=self.user,
            name='Test Provider',
            provider_type=ProviderType.OLLAMA,
            model='llama3'
        )
    
    def test_create_agent(self):
        agent = Agent.objects.create(
            user=self.user,
            name='Test Agent',
            llm_provider=self.provider,
            system_prompt='You are helpful.'
        )
        self.assertEqual(agent.name, 'Test Agent')
        self.assertEqual(str(agent), 'Test Agent')
    
    def test_is_tool_requires_description(self):
        agent = Agent(
            user=self.user,
            name='Tool Agent',
            llm_provider=self.provider,
            system_prompt='Tool prompt',
            is_tool=True
        )
        with self.assertRaises(ValidationError):
            agent.full_clean()
    
    def test_cycle_detection(self):
        agent1 = Agent.objects.create(user=self.user, name='Agent1', llm_provider=self.provider, system_prompt='P1', is_tool=True, tool_description='D1')
        agent2 = Agent.objects.create(user=self.user, name='Agent2', llm_provider=self.provider, system_prompt='P2', is_tool=True, tool_description='D2')
        agent3 = Agent.objects.create(user=self.user, name='Agent3', llm_provider=self.provider, system_prompt='P3', is_tool=True, tool_description='D3')
        
        # Create cycle: A1 -> A2 -> A3 -> A1
        agent1.agent_tools.add(agent2)
        agent2.agent_tools.add(agent3)
        agent3.agent_tools.add(agent1)
        
        with self.assertRaises(ValidationError):
            agent1.full_clean()  # Cycle should be detected
    
    def test_no_cycle(self):
        agent1 = Agent.objects.create(user=self.user, name='Agent1', llm_provider=self.provider, system_prompt='P1', is_tool=True, tool_description='D1')
        agent2 = Agent.objects.create(user=self.user, name='Agent2', llm_provider=self.provider, system_prompt='P2', is_tool=True, tool_description='D2')
        agent1.agent_tools.add(agent2)  # No cycle
        agent1.full_clean()  # Should pass
    
    def test_auto_set_default_agent(self):
        agent = Agent.objects.create(
            user=self.user,
            name='First Agent',
            llm_provider=self.provider,
            system_prompt='Prompt',
            is_tool=False  # Not a tool, so should auto-set as default
        )
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.default_agent, agent)

class UserProfileModelTest(BaseTestCase):
    def test_create_profile(self):
        # Profile is auto-created via signal
        profile = UserProfile.objects.get(user=self.user)
        self.assertIsNotNone(profile)

class ToolCredentialModelTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.tool = Tool.objects.create(
            user=self.user,
            name='Test Tool',
            description='Test',
            tool_type=Tool.ToolType.API,
            endpoint='https://api.example.com'
        )
    
    def test_create_credential(self):
        cred = ToolCredential.objects.create(
            user=self.user,
            tool=self.tool,
            auth_type='basic',
            username='test',
            password='secret'
        )
        self.assertEqual(cred.auth_type, 'basic')
        self.assertEqual(str(cred), f"{self.user.username}'s credentials for {self.tool.name}")
    
    def test_unique_together(self):
        ToolCredential.objects.create(user=self.user, tool=self.tool, auth_type='basic')
        with self.assertRaises(ValidationError):
            duplicate = ToolCredential(user=self.user, tool=self.tool, auth_type='token')
            duplicate.full_clean()

class ThreadModelTest(BaseTestCase):
    def test_create_thread(self):
        thread = Thread.objects.create(user=self.user, subject='Test Subject')
        self.assertEqual(thread.subject, 'Test Subject')
        self.assertEqual(str(thread), 'Test Subject')
    
    def test_add_message(self):
        thread = Thread.objects.create(user=self.user, subject='Test')
        message = thread.add_message('Hello', Actor.USER)
        self.assertEqual(message.text, 'Hello')
        self.assertEqual(message.actor, Actor.USER)
        self.assertEqual(message.thread, thread)
    
    def test_add_message_invalid_actor(self):
        thread = Thread.objects.create(user=self.user, subject='Test')
        with self.assertRaises(ValueError):
            thread.add_message('Invalid', 'INVALID')
    
    def test_get_messages(self):
        thread = Thread.objects.create(user=self.user, subject='Test')
        thread.add_message('Msg1', Actor.USER)
        thread.add_message('Msg2', Actor.AGENT)
        messages = thread.get_messages()
        self.assertEqual(messages.count(), 2)
    
    @patch('nova.models.boto3.client')
    def test_thread_deletion_cleans_up_files(self, mock_boto3_client):
        """Test that deleting a thread also deletes associated files from MinIO."""
        # Mock the S3 client
        mock_s3_client = mock_boto3_client.return_value
        
        # Create a thread
        thread = Thread.objects.create(user=self.user, subject='Test Thread')
        
        # Create some test files associated with the thread
        file1 = UserFile.objects.create(
            user=self.user,
            thread=thread,
            key='users/1/threads/1/file1.txt',
            original_filename='file1.txt',
            mime_type='text/plain',
            size=100
        )
        file2 = UserFile.objects.create(
            user=self.user,
            thread=thread,
            key='users/1/threads/1/file2.txt',
            original_filename='file2.txt',
            mime_type='text/plain',
            size=200
        )
        
        # Verify files exist before deletion
        self.assertEqual(UserFile.objects.filter(thread=thread).count(), 2)
        
        # Delete the thread (this should trigger our signal)
        thread.delete()
        
        # Verify files were deleted from database
        self.assertEqual(UserFile.objects.filter(key__in=['users/1/threads/1/file1.txt', 'users/1/threads/1/file2.txt']).count(), 0)
        
        # Verify MinIO delete_object was called for each file
        self.assertEqual(mock_s3_client.delete_object.call_count, 2)
        
        # Verify the correct keys were deleted from MinIO
        expected_calls = [
            patch.call(Bucket=patch.ANY, Key='users/1/threads/1/file1.txt'),
            patch.call(Bucket=patch.ANY, Key='users/1/threads/1/file2.txt')
        ]
        mock_s3_client.delete_object.assert_has_calls(expected_calls, any_order=True)
    
    @patch('nova.models.boto3.client')
    def test_thread_deletion_handles_minio_errors(self, mock_boto3_client):
        """Test that thread deletion continues even if MinIO deletion fails."""
        # Mock the S3 client to raise an error
        mock_s3_client = mock_boto3_client.return_value
        mock_s3_client.delete_object.side_effect = Exception("MinIO connection error")
        
        # Create a thread with a file
        thread = Thread.objects.create(user=self.user, subject='Test Thread')
        file1 = UserFile.objects.create(
            user=self.user,
            thread=thread,
            key='users/1/threads/1/file1.txt',
            original_filename='file1.txt',
            mime_type='text/plain',
            size=100
        )
        
        # Delete the thread - should not raise an exception despite MinIO error
        thread.delete()
        
        # Verify thread was deleted despite MinIO error
        self.assertFalse(Thread.objects.filter(id=thread.id).exists())
        
        # Verify file was still deleted from database
        self.assertEqual(UserFile.objects.filter(key='users/1/threads/1/file1.txt').count(), 0)

class TaskModelTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject='Test Thread')
        self.agent = Agent.objects.create(
            user=self.user,
            name='Test Agent',
            llm_provider=LLMProvider.objects.create(
                user=self.user, name='Provider', provider_type=ProviderType.OLLAMA, model='llama3'
            ),
            system_prompt='Prompt'
        )
    
    def test_create_task(self):
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            agent=self.agent,
            status=TaskStatus.PENDING
        )
        self.assertEqual(task.status, TaskStatus.PENDING)
        self.assertEqual(str(task), f"Task {task.id} for Thread {self.thread.subject} ({task.status})")
    
    def test_progress_logs_default(self):
        task = Task.objects.create(user=self.user, thread=self.thread)
        self.assertEqual(task.progress_logs, [])
