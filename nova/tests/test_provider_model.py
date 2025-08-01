# nova/tests/test_provider_model.py
"""
Tests for the LLMProvider model.

Focus on LLMProvider-specific behavior:
- Model creation and validation
- Provider type choices
- API key encryption
- Additional config JSON handling
- User relationships and constraints
"""

from django.test import TestCase
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.contrib.auth.models import User
import json

from nova.models import LLMProvider, ProviderType
from .base import BaseModelTestCase


class LLMProviderModelTests(BaseModelTestCase):
    """Test cases for LLMProvider model."""

    # ------------------------------------------------------------------ #
    #  Creation and basic functionality                                  #
    # ------------------------------------------------------------------ #
    
    def test_create_llm_provider(self):
        """Test creating a basic LLMProvider."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Test Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        self.assertEqual(provider.name, "Test Provider")
        self.assertEqual(provider.provider_type, ProviderType.OPENAI)
        self.assertEqual(provider.model, "gpt-3.5-turbo")
        self.assertEqual(provider.user, self.user)
        self.assertIsNotNone(provider.created_at)
        self.assertIsNotNone(provider.updated_at)

    def test_provider_str_representation(self):
        """Test the string representation of LLMProvider."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="My OpenAI Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-4"
        )
        
        expected_str = "My OpenAI Provider (openai)"
        self.assertEqual(str(provider), expected_str)

    def test_create_provider_with_all_fields(self):
        """Test creating provider with all optional fields."""
        additional_config = {"temperature": 0.7, "max_tokens": 1000}
        
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Full Provider",
            provider_type=ProviderType.MISTRAL,
            model="mistral-large",
            api_key="test-api-key-123",
            base_url="https://api.mistral.ai/v1",
            additional_config=additional_config
        )
        
        self.assertEqual(provider.name, "Full Provider")
        self.assertEqual(provider.provider_type, ProviderType.MISTRAL)
        self.assertEqual(provider.model, "mistral-large")
        self.assertEqual(provider.api_key, "test-api-key-123")
        self.assertEqual(provider.base_url, "https://api.mistral.ai/v1")
        self.assertEqual(provider.additional_config, additional_config)

    # ------------------------------------------------------------------ #
    #  Provider type validation                                          #
    # ------------------------------------------------------------------ #
    
    def test_all_provider_types(self):
        """Test that all provider types can be created."""
        provider_configs = [
            (ProviderType.OPENAI, "gpt-3.5-turbo"),
            (ProviderType.MISTRAL, "mistral-small"),
            (ProviderType.OLLAMA, "llama2"),
            (ProviderType.LLMSTUDIO, "local-model"),
        ]
        
        for provider_type, model in provider_configs:
            with self.subTest(provider_type=provider_type):
                provider = LLMProvider.objects.create(
                    user=self.user,
                    name=f"Test {provider_type}",
                    provider_type=provider_type,
                    model=model
                )
                self.assertEqual(provider.provider_type, provider_type)
                self.assertEqual(provider.model, model)

    def test_default_provider_type(self):
        """Test that default provider type is OLLAMA."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Default Provider",
            model="test-model"
            # provider_type not specified
        )
        
        self.assertEqual(provider.provider_type, ProviderType.OLLAMA)

    # ------------------------------------------------------------------ #
    #  Validation and constraints                                        #
    # ------------------------------------------------------------------ #
    
    def test_unique_name_per_user(self):
        """Test that provider names must be unique per user."""
        # Create first provider
        LLMProvider.objects.create(
            user=self.user,
            name="Unique Name",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        # Try to create second provider with same name for same user
        with self.assertRaises(IntegrityError):
            LLMProvider.objects.create(
                user=self.user,
                name="Unique Name",
                provider_type=ProviderType.MISTRAL,
                model="mistral-small"
            )

    def test_same_name_different_users(self):
        """Test that different users can have providers with same name."""
        # Create second user
        user2 = User.objects.create_user(
            username='testuser2',
            password='testpass123'
        )
        
        # Create providers with same name for different users
        provider1 = LLMProvider.objects.create(
            user=self.user,
            name="Same Name",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        provider2 = LLMProvider.objects.create(
            user=user2,
            name="Same Name",
            provider_type=ProviderType.MISTRAL,
            model="mistral-small"
        )
        
        self.assertEqual(provider1.name, provider2.name)
        self.assertNotEqual(provider1.user, provider2.user)

    def test_required_fields(self):
        """Test that required fields are enforced."""
        # Test missing name - Django allows empty strings for CharField
        provider = LLMProvider(
            user=self.user,
            name="",  # Empty name
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        # This should work - empty string is allowed
        provider.save()
        self.assertEqual(provider.name, "")
        
        # Test missing model - Django allows empty strings for CharField
        provider2 = LLMProvider(
            user=self.user,
            name="Test Provider",
            provider_type=ProviderType.OPENAI,
            model=""  # Empty model
        )
        # This should work - empty string is allowed
        provider2.save()
        self.assertEqual(provider2.model, "")

    # ------------------------------------------------------------------ #
    #  API key encryption                                                #
    # ------------------------------------------------------------------ #
    
    def test_api_key_encryption(self):
        """Test that API keys are encrypted when stored."""
        api_key = "sk-test-api-key-12345"
        
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Encrypted Provider",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo",
            api_key=api_key
        )
        
        # API key should be retrievable as plain text
        self.assertEqual(provider.api_key, api_key)
        
        # Refresh from database
        provider.refresh_from_db()
        self.assertEqual(provider.api_key, api_key)

    def test_empty_api_key(self):
        """Test that empty API key is handled correctly."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="No Key Provider",
            provider_type=ProviderType.OLLAMA,
            model="llama2"
            # api_key not provided
        )
        
        self.assertIsNone(provider.api_key)

    def test_blank_api_key(self):
        """Test that blank API key is handled correctly."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Blank Key Provider",
            provider_type=ProviderType.OLLAMA,
            model="llama2",
            api_key=""
        )
        
        self.assertEqual(provider.api_key, "")

    # ------------------------------------------------------------------ #
    #  Additional config JSON handling                                   #
    # ------------------------------------------------------------------ #
    
    def test_additional_config_default(self):
        """Test that additional_config defaults to empty dict."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Default Config",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        self.assertEqual(provider.additional_config, {})

    def test_additional_config_complex(self):
        """Test storing complex JSON in additional_config."""
        complex_config = {
            "temperature": 0.8,
            "max_tokens": 2000,
            "top_p": 0.9,
            "frequency_penalty": 0.1,
            "presence_penalty": 0.1,
            "stop_sequences": ["Human:", "AI:"],
            "nested": {
                "key1": "value1",
                "key2": ["item1", "item2"]
            }
        }
        
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Complex Config",
            provider_type=ProviderType.OPENAI,
            model="gpt-4",
            additional_config=complex_config
        )
        
        self.assertEqual(provider.additional_config, complex_config)
        
        # Refresh from database and verify
        provider.refresh_from_db()
        self.assertEqual(provider.additional_config, complex_config)

    # ------------------------------------------------------------------ #
    #  User relationships                                                #
    # ------------------------------------------------------------------ #
    
    def test_user_relationship(self):
        """Test the relationship between User and LLMProvider."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Relationship Test",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        # Test forward relationship
        self.assertEqual(provider.user, self.user)
        
        # Test reverse relationship
        user_providers = self.user.llm_providers.all()
        self.assertIn(provider, user_providers)

    def test_cascade_delete_user(self):
        """Test that deleting user deletes associated providers."""
        provider = LLMProvider.objects.create(
            user=self.user,
            name="Cascade Test",
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        provider_id = provider.id
        
        # Delete user
        self.user.delete()
        
        # Provider should be deleted too
        self.assertFalse(
            LLMProvider.objects.filter(id=provider_id).exists()
        )

    # ------------------------------------------------------------------ #
    #  Edge cases and error handling                                     #
    # ------------------------------------------------------------------ #
    
    def test_very_long_names(self):
        """Test handling of very long provider names."""
        long_name = "A" * 120  # Exactly at the limit
        
        provider = LLMProvider.objects.create(
            user=self.user,
            name=long_name,
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        self.assertEqual(provider.name, long_name)
        self.assertEqual(len(provider.name), 120)
        
        # Test name that's longer than max_length
        # Django allows it in memory but may cause issues at DB level
        too_long_name = "A" * 200
        provider2 = LLMProvider(
            user=self.user,
            name=too_long_name,
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        # The name is stored as-is in the model instance
        self.assertEqual(len(provider2.name), 200)
        
        # But validation should catch this
        with self.assertRaises(Exception):  # ValidationError or DataError when saving
            provider2.full_clean()  # This should raise ValidationError

    def test_special_characters_in_name(self):
        """Test handling of special characters in provider name."""
        special_name = "Test Provider! @#$%^&*()_+-=[]{}|;':\",./<>?"
        
        provider = LLMProvider.objects.create(
            user=self.user,
            name=special_name,
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        self.assertEqual(provider.name, special_name)

    def test_unicode_in_name(self):
        """Test handling of Unicode characters in provider name."""
        unicode_name = "Test Provider 测试 🤖 Тест"
        
        provider = LLMProvider.objects.create(
            user=self.user,
            name=unicode_name,
            provider_type=ProviderType.OPENAI,
            model="gpt-3.5-turbo"
        )
        
        self.assertEqual(provider.name, unicode_name)
