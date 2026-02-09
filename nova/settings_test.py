"""
Test-specific Django settings for Nova project.
This file inherits from the main settings and overrides configurations
to enable local testing without external Docker services.
"""
from .settings import *
import tempfile

# Override database to use SQLite in-memory for fast testing
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
    }
}

# Use in-memory channel layer for testing (no Redis required)
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}

# Override MinIO settings to use local file storage for testing
MEDIA_ROOT = tempfile.mkdtemp()  # Temporary directory for test files

# Ensure file expiration logic is enabled for model tests by default
# (production can disable via USERFILE_EXPIRATION_DAYS env var)
USERFILE_EXPIRATION_DAYS = 30

# Disable MinIO validation for tests
MINIO_ENDPOINT_URL = 'http://localhost:9000'  # Dummy value
MINIO_ACCESS_KEY = 'test_access_key'  # Dummy value
MINIO_SECRET_KEY = 'test_secret_key'  # Dummy value
MINIO_BUCKET_NAME = 'test-bucket'
MINIO_SECURE = False

# Keep encryption key for consistency (tests may depend on it)
# FIELD_ENCRYPTION_KEY is already set from main settings

# Disable CSRF validation for easier testing
CSRF_TRUSTED_ORIGINS = ['http://localhost', 'http://testserver']

# Ensure DEBUG is False for testing (more realistic)
DEBUG = False

# Override ALLOWED_HOSTS for testing
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'testserver']

# Disable any external service integrations that might cause issues
# Add any other service-specific overrides here as needed

# Speed up password hashing for tests
PASSWORD_HASHERS = [
    'django.contrib.auth.hashers.MD5PasswordHasher',
]


# Disable migrations for faster test database creation
class DisableMigrations:
    def __contains__(self, item):
        return True

    def __getitem__(self, item):
        return None


# Logging configuration for tests (optional - reduces noise)
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'ERROR',
    },
    'loggers': {
        # 404/400 request logs are expected in many negative-path tests.
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        # Silence asyncio slow-task warnings in CI/test environments.
        'asyncio': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

# Test-specific settings
TEST_RUNNER = 'django.test.runner.DiscoverRunner'

print("Using test settings with SQLite in-memory database")
