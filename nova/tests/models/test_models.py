# nova/tests/models/test_models.py
from datetime import timedelta
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.utils import timezone
from unittest.mock import patch, MagicMock

from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
from nova.models.Interaction import Interaction, InteractionStatus
from nova.models.Message import Message, Actor, MessageType
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.models.UserObjects import UserInfo, UserParameters, UserProfile
from nova.tests.base import BaseTestCase
from nova.tests.factories import create_agent, create_provider, create_user


class UserObjectsModelsTest(BaseTestCase):
    def test_user_info_creation(self):
        """
        Test UserInfo model creation and default values.
        Ensures that UserInfo objects are properly created with default markdown content.
        """
        # UserInfo is created automatically via signal, so we get it for user created in base.py
        user_info = UserInfo.objects.get(user=self.user)
        self.assertEqual(user_info.user, self.user)
        self.assertEqual(user_info.markdown_content, "# global_user_preferences\n")

    def test_user_info_clean_valid_markdown(self):
        """
        Test UserInfo validation with valid markdown content.
        Verifies that clean() method accepts properly formatted markdown with themes.
        """
        user_info = UserInfo(user=self.user, markdown_content="# global_user_preferences\n# theme1\n")
        user_info.clean()  # Should not raise

    def test_user_info_clean_missing_global_theme(self):
        """
        Test UserInfo validation when global_user_preferences theme is missing.
        Ensures that the required global theme cannot be deleted.
        """
        user_info = UserInfo(user=self.user, markdown_content="# other_theme\n")
        with self.assertRaises(ValidationError):
            user_info.full_clean()

    def test_user_info_clean_too_long_content(self):
        """
        Test UserInfo validation with content exceeding maximum length.
        Verifies that content is limited to 50,000 characters.
        """
        content = "# global_user_preferences\n" + "x" * 50001
        user_info = UserInfo(user=self.user, markdown_content=content)
        with self.assertRaises(ValidationError):
            user_info.full_clean()

    def test_user_info_get_themes(self):
        """
        Test UserInfo.get_themes() method extracts theme names correctly.
        Verifies that themes are parsed from markdown headings.
        """
        user_info = UserInfo(
            user=self.user,
            markdown_content="# global_user_preferences\n# theme1\n# theme2\n"
        )
        themes = user_info.get_themes()
        self.assertEqual(themes, ["global_user_preferences", "theme1", "theme2"])

    def test_user_parameters_creation(self):
        """
        Test UserParameters model creation and default values.
        Ensures that UserParameters objects are properly initialized.
        """
        # UserParameters is created automatically via signal, so we get it for user created in base.py
        params = UserParameters.objects.get(user=self.user)
        self.assertEqual(params.user, self.user)
        self.assertFalse(params.allow_langfuse)

    def test_user_profile_creation(self):
        """
        Test UserProfile model creation and default values.
        Verifies that UserProfile objects start with no default agent.
        """
        # UserProfile is created automatically via signal, so we get it for user created in base.py
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.user, self.user)
        self.assertIsNone(profile.default_agent)

    def test_user_profile_clean_valid_default_agent(self):
        """
        Test UserProfile validation with valid default agent.
        Ensures that a normal agent can be set as default.
        """
        # Create a new user for this test to avoid uniqueness constraint
        other_user = create_user("otheruser2")
        provider = create_provider(other_user)
        agent = create_agent(other_user, provider)
        # Don't save, just test validation - but full_clean() checks uniqueness
        # So we need to test the clean method directly instead of full_clean
        profile = UserProfile(user=other_user, default_agent=agent)
        profile.clean()  # Should not raise

    def test_user_profile_clean_tool_as_default(self):
        """
        Test UserProfile validation prevents tool agents from being default.
        Ensures that only normal agents can be set as default.
        """
        provider = create_provider(self.user)
        agent = create_agent(self.user, provider, is_tool=True, tool_description="test")
        profile = UserProfile(user=self.user, default_agent=agent)
        with self.assertRaises(ValidationError):
            profile.full_clean()

    def test_user_profile_clean_wrong_user_agent(self):
        """
        Test UserProfile validation prevents cross-user agent assignment.
        Ensures that default agent must belong to the same user.
        """
        other_user = create_user("other")
        provider = create_provider(other_user)
        agent = create_agent(other_user, provider)
        profile = UserProfile(user=self.user, default_agent=agent)
        with self.assertRaises(ValidationError):
            profile.full_clean()


class AgentConfigModelsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.provider = create_provider(self.user)

    def test_agent_config_creation(self):
        """
        Test AgentConfig model creation with valid parameters.
        Ensures that agent configurations are created correctly.
        """
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=self.provider,
            system_prompt="You are a helpful assistant.",
        )
        self.assertEqual(agent.user, self.user)
        self.assertEqual(agent.name, "Test Agent")
        self.assertEqual(agent.llm_provider, self.provider)

    def test_agent_config_str(self):
        """
        Test AgentConfig string representation.
        Verifies that __str__ returns the agent name.
        """
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=self.provider,
            system_prompt="Test",
        )
        self.assertEqual(str(agent), "Test Agent")

    def test_agent_config_clean_valid(self):
        """
        Test AgentConfig validation with valid parameters.
        Ensures that normal agents pass validation.
        """
        agent = AgentConfig(
            user=self.user,
            name="Test",
            llm_provider=self.provider,
            system_prompt="Test",
        )
        agent.full_clean()  # Should not raise

    def test_agent_config_clean_tool_missing_description(self):
        """
        Test AgentConfig validation for tool agents without description.
        Ensures that tool agents require a description.
        """
        agent = AgentConfig(
            user=self.user,
            name="Test",
            llm_provider=self.provider,
            system_prompt="Test",
            is_tool=True,
        )
        with self.assertRaises(ValidationError):
            agent.full_clean()

    def test_agent_config_clean_tool_with_description(self):
        """
        Test AgentConfig validation for tool agents with description.
        Ensures that tool agents with descriptions pass validation.
        """
        agent = AgentConfig(
            user=self.user,
            name="Test",
            llm_provider=self.provider,
            system_prompt="Test",
            is_tool=True,
            tool_description="Test description",
        )
        agent.full_clean()  # Should not raise

    def test_agent_config_save_sets_default_agent(self):
        """
        Test AgentConfig save method sets first normal agent as default.
        Verifies that the first created normal agent becomes the default.
        """
        agent = AgentConfig.objects.create(
            user=self.user,
            name="Test Agent",
            llm_provider=self.provider,
            system_prompt="Test",
        )
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.default_agent, agent)

    def test_agent_config_save_removes_tool_from_default(self):
        """
        Test AgentConfig save method removes tool agents from default.
        Ensures that when an agent becomes a tool, it is removed from the user's default agent.
        """
        # First create a normal agent as default
        normal_agent = AgentConfig.objects.create(
            user=self.user,
            name="Normal Agent",
            llm_provider=self.provider,
            system_prompt="Test",
        )
        profile = UserProfile.objects.get(user=self.user)
        profile.default_agent = normal_agent
        profile.save()

        # Now make it a tool - the save method should remove it from default
        normal_agent.is_tool = True
        normal_agent.tool_description = "Test tool"
        normal_agent.save()

        # Re-fetch the profile to see the updated state
        profile = UserProfile.objects.get(user=self.user)
        # The save method should have set default_agent to None because the agent became a tool
        self.assertIsNone(profile.default_agent)

    def test_agent_config_has_cycle_no_cycle(self):
        """
        Test AgentConfig cycle detection with acyclic dependencies.
        Ensures that valid agent tool relationships don't trigger cycle detection.
        """
        agent1 = create_agent(self.user, self.provider, name="Agent1")
        agent2 = create_agent(self.user, self.provider, name="Agent2", is_tool=True, tool_description="test")
        agent1.agent_tools.add(agent2)
        self.assertFalse(agent1._has_cycle())

    def test_agent_config_has_cycle_simple_cycle(self):
        """
        Test AgentConfig cycle detection with circular dependencies.
        Ensures that cycles between agents are properly detected.
        """
        agent1 = create_agent(self.user, self.provider, name="Agent1", is_tool=True, tool_description="test")
        agent2 = create_agent(self.user, self.provider, name="Agent2", is_tool=True, tool_description="test")
        agent1.agent_tools.add(agent2)
        agent2.agent_tools.add(agent1)
        self.assertTrue(agent1._has_cycle())

    def test_agent_config_clean_detects_cycle(self):
        """
        Test AgentConfig validation prevents cyclic dependencies.
        Ensures that cycles are detected during model validation.
        """
        agent1 = create_agent(self.user, self.provider, name="Agent1", is_tool=True, tool_description="test")
        agent2 = create_agent(self.user, self.provider, name="Agent2", is_tool=True, tool_description="test")
        agent1.agent_tools.add(agent2)
        agent2.agent_tools.add(agent1)
        with self.assertRaises(ValidationError):
            agent1.full_clean()


class ThreadModelsTest(BaseTestCase):
    def test_thread_creation(self):
        """
        Test Thread model creation.
        Ensures that threads are properly associated with users.
        """
        thread = Thread.objects.create(user=self.user, subject="Test Thread")
        self.assertEqual(thread.user, self.user)
        self.assertEqual(thread.subject, "Test Thread")

    def test_thread_str(self):
        """
        Test Thread string representation.
        Verifies that __str__ returns the thread subject.
        """
        thread = Thread.objects.create(user=self.user, subject="Test Thread")
        self.assertEqual(str(thread), "Test Thread")

    def test_thread_add_message(self):
        """
        Test Thread.add_message() method for standard messages.
        Ensures that messages are created and linked to the thread.
        """
        thread = Thread.objects.create(user=self.user, subject="Test Thread")
        message = thread.add_message("Test message", Actor.USER)
        self.assertEqual(message.text, "Test message")
        self.assertEqual(message.actor, Actor.USER)
        self.assertEqual(message.thread, thread)

    def test_thread_add_message_invalid_actor(self):
        """
        Test Thread.add_message() validation for invalid actors.
        Ensures that only valid Actor enum values are accepted.
        """
        thread = Thread.objects.create(user=self.user, subject="Test Thread")
        with self.assertRaises(ValueError):
            thread.add_message("Test", "INVALID")

    def test_thread_add_message_with_interaction(self):
        """
        Test Thread.add_message() method with interaction context.
        Ensures that messages can be linked to interactions.
        """
        thread = Thread.objects.create(user=self.user, subject="Test Thread")
        task = Task.objects.create(user=self.user, thread=thread)
        provider = create_provider(self.user)
        agent = create_agent(self.user, provider)
        interaction = Interaction.objects.create(
            task=task,
            thread=thread,
            agent_config=agent,
            question="Test question",
        )
        message = thread.add_message("Test answer", Actor.USER, interaction=interaction)
        self.assertEqual(message.interaction, interaction)

    def test_thread_get_messages(self):
        """
        Test Thread.get_messages() method returns ordered messages.
        Ensures that messages are returned in creation order.
        """
        thread = Thread.objects.create(user=self.user, subject="Test Thread")
        thread.add_message("Message 1", Actor.USER)
        thread.add_message("Message 2", Actor.AGENT)
        messages = thread.get_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].text, "Message 1")
        self.assertEqual(messages[1].text, "Message 2")


class MessageModelsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Test Thread")

    def test_message_creation(self):
        """
        Test Message model creation with basic fields.
        Ensures that messages are properly linked to users and threads.
        """
        message = Message.objects.create(
            user=self.user,
            text="Test message",
            actor=Actor.USER,
            thread=self.thread,
        )
        self.assertEqual(message.user, self.user)
        self.assertEqual(message.text, "Test message")
        self.assertEqual(message.actor, Actor.USER)

    def test_message_str(self):
        """
        Test Message string representation.
        Verifies that __str__ returns the message text.
        """
        message = Message.objects.create(
            user=self.user,
            text="Test message",
            actor=Actor.USER,
            thread=self.thread,
        )
        self.assertEqual(str(message), "Test message")

    def test_message_with_interaction(self):
        """
        Test Message model with interaction relationships.
        Ensures that messages can be linked to agent-user interactions.
        """
        task = Task.objects.create(user=self.user, thread=self.thread, agent=None)
        provider = create_provider(self.user)
        agent = create_agent(self.user, provider)
        interaction = Interaction.objects.create(
            task=task,
            thread=self.thread,
            agent_config=agent,
            question="Test question",
        )
        message = Message.objects.create(
            user=self.user,
            text="Test answer",
            actor=Actor.USER,
            thread=self.thread,
            message_type=MessageType.INTERACTION_ANSWER,
            interaction=interaction,
        )
        self.assertEqual(message.interaction, interaction)
        self.assertEqual(message.message_type, MessageType.INTERACTION_ANSWER)


class TaskModelsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Test Thread")

    def test_task_creation(self):
        """
        Test Task model creation for async agent execution.
        Ensures that tasks are properly linked to users and threads.
        """
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            status=TaskStatus.PENDING,
        )
        self.assertEqual(task.user, self.user)
        self.assertEqual(task.thread, self.thread)
        self.assertEqual(task.status, TaskStatus.PENDING)

    def test_task_str(self):
        """
        Test Task string representation.
        Verifies that __str__ includes thread subject and status.
        """
        task = Task.objects.create(
            user=self.user,
            thread=self.thread,
            status=TaskStatus.RUNNING,
        )
        self.assertIn("Test Thread", str(task))
        self.assertIn("RUNNING", str(task))


class InteractionModelsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Test Thread")
        self.task = Task.objects.create(user=self.user, thread=self.thread)
        self.provider = create_provider(self.user)
        self.agent = create_agent(self.user, self.provider)

    def test_interaction_creation(self):
        """
        Test Interaction model creation for agent-user questions.
        Ensures that interactions are properly linked to tasks and threads.
        """
        interaction = Interaction.objects.create(
            task=self.task,
            thread=self.thread,
            agent_config=self.agent,
            question="Test question",
        )
        self.assertEqual(interaction.task, self.task)
        self.assertEqual(interaction.thread, self.thread)
        self.assertEqual(interaction.question, "Test question")
        self.assertEqual(interaction.status, InteractionStatus.PENDING)

    def test_interaction_str(self):
        """
        Test Interaction string representation.
        Verifies that __str__ includes origin name and question.
        """
        interaction = Interaction.objects.create(
            task=self.task,
            thread=self.thread,
            agent_config=self.agent,
            question="Test question",
            origin_name="Test Agent",
        )
        self.assertIn("Test Agent", str(interaction))
        self.assertIn("Test question", str(interaction))

    def test_interaction_clean_valid(self):
        """
        Test Interaction validation with valid parameters.
        Ensures that properly configured interactions pass validation.
        """
        interaction = Interaction(
            task=self.task,
            thread=self.thread,
            agent_config=self.agent,
            question="Test",
        )
        interaction.full_clean()  # Should not raise

    def test_interaction_clean_wrong_thread(self):
        """
        Test Interaction validation prevents mismatched threads.
        Ensures that interaction thread matches task thread.
        """
        other_thread = Thread.objects.create(user=self.user, subject="Other")
        interaction = Interaction(
            task=self.task,
            thread=other_thread,
            agent_config=self.agent,
            question="Test",
        )
        with self.assertRaises(ValidationError):
            interaction.full_clean()

    def test_interaction_clean_duplicate_pending(self):
        """
        Test Interaction validation prevents multiple pending interactions per task.
        Ensures that only one pending interaction exists per task.
        """
        Interaction.objects.create(
            task=self.task,
            thread=self.thread,
            agent_config=self.agent,
            question="First",
        )
        interaction = Interaction(
            task=self.task,
            thread=self.thread,
            agent_config=self.agent,
            question="Second",
        )
        with self.assertRaises(ValidationError):
            interaction.full_clean()


class CheckpointLinkModelsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Test Thread")
        self.provider = create_provider(self.user)
        self.agent = create_agent(self.user, self.provider)

    def test_checkpoint_link_creation(self):
        """
        Test CheckpointLink model creation for LangGraph state persistence.
        Ensures that checkpoint links are properly created with UUIDs.
        """
        link = CheckpointLink.objects.create(
            thread=self.thread,
            agent=self.agent,
        )
        self.assertEqual(link.thread, self.thread)
        self.assertEqual(link.agent, self.agent)
        self.assertIsNotNone(link.checkpoint_id)

    def test_checkpoint_link_str(self):
        """
        Test CheckpointLink string representation.
        Verifies that __str__ includes checkpoint and entity IDs.
        """
        link = CheckpointLink.objects.create(
            thread=self.thread,
            agent=self.agent,
        )
        self.assertIn("Checkpoint", str(link))
        self.assertIn(str(self.thread.id), str(link))
        self.assertIn(str(self.agent.id), str(link))


class UserFileModelsTest(BaseTestCase):
    def setUp(self):
        super().setUp()
        self.thread = Thread.objects.create(user=self.user, subject="Test Thread")

    def test_user_file_creation(self):
        """
        Test UserFile model creation for file storage.
        Ensures that files are properly linked to users and threads with expiration.
        """
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key="test-key",
            original_filename="test.txt",
            mime_type="text/plain",
            size=100,
        )
        self.assertEqual(user_file.user, self.user)
        self.assertEqual(user_file.thread, self.thread)
        self.assertEqual(user_file.original_filename, "test.txt")
        self.assertIsNotNone(user_file.expiration_date)

    def test_user_file_str(self):
        """
        Test UserFile string representation.
        Verifies that __str__ shows filename and key.
        """
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key="test-key",
            original_filename="test.txt",
            mime_type="text/plain",
            size=100,
        )
        self.assertEqual(str(user_file), "test.txt (test-key)")

    def test_user_file_save_generates_key(self):
        """
        Test UserFile save method generates proper S3 keys.
        Ensures that keys follow the expected user/thread/filename pattern.
        """
        user_file = UserFile(
            user=self.user,
            thread=self.thread,
            original_filename="test.txt",
            mime_type="text/plain",
            size=100,
        )
        user_file.save()
        expected_key = f"users/{self.user.id}/threads/{self.thread.id}test.txt"
        self.assertEqual(user_file.key, expected_key)

    def test_user_file_get_download_url_expired(self):
        """
        Test UserFile download URL generation for expired files.
        Ensures that expired files are deleted and ValueError is raised.
        """
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key="test-key",
            original_filename="test.txt",
            mime_type="text/plain",
            size=100,
        )
        # Set expiration to past
        user_file.expiration_date = timezone.now() - timedelta(days=1)
        user_file.save()

        # Mock boto3.client to avoid actual MinIO connection
        with patch('boto3.client') as mock_boto3_client:
            mock_s3_client = MagicMock()
            mock_boto3_client.return_value = mock_s3_client

            # The method should check expiration and raise ValueError
            with self.assertRaises(ValueError) as context:
                user_file.get_download_url()

            self.assertIn("File expired and deleted", str(context.exception))

            # Verify the file was deleted from database
            with self.assertRaises(UserFile.DoesNotExist):
                UserFile.objects.get(pk=user_file.pk)

    @override_settings(
        MINIO_ENDPOINT_URL='http://minio:9000',
        MINIO_ACCESS_KEY='test-key',
        MINIO_SECRET_KEY='test-secret',
        MINIO_BUCKET_NAME='test-bucket',
        CSRF_TRUSTED_ORIGINS=['http://localhost:8080']
    )
    def test_user_file_get_download_url_valid(self):
        """
        Test UserFile download URL generation for valid (non-expired) files.
        Ensures that presigned URLs are generated correctly with mocked MinIO.
        """
        user_file = UserFile.objects.create(
            user=self.user,
            thread=self.thread,
            key="test-key",
            original_filename="test.txt",
            mime_type="text/plain",
            size=100,
        )

        # Mock boto3.client and its methods
        with patch('boto3.client') as mock_boto3_client:
            mock_s3_client = MagicMock()
            mock_boto3_client.return_value = mock_s3_client

            # Mock the presigned URL generation
            mock_s3_client.generate_presigned_url.return_value = 'http://minio:9000/test-bucket/test-key?signature=mock'

            url = user_file.get_download_url()

            # Verify the URL was modified to use external base
            self.assertEqual(url, 'http://localhost:8080/test-bucket/test-key?signature=mock')

            # Verify boto3.client was called with correct parameters
            mock_boto3_client.assert_called_once_with(
                's3',
                endpoint_url='http://minio:9000',
                aws_access_key_id='test-key',
                aws_secret_access_key='test-secret',
                config=mock_boto3_client.call_args[1]['config']  # Config object
            )

            # Verify generate_presigned_url was called correctly
            mock_s3_client.generate_presigned_url.assert_called_once_with(
                'get_object',
                Params={'Bucket': 'test-bucket', 'Key': 'test-key'},
                ExpiresIn=3600
            )
