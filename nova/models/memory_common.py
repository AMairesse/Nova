from django.db import models
from django.utils.translation import gettext_lazy as _


MEMORY_EMBEDDING_DIMENSIONS = 1024


class MemoryRecordStatus(models.TextChoices):
    ACTIVE = "active", _("active")
    ARCHIVED = "archived", _("archived")


class MemoryChunkEmbeddingState(models.TextChoices):
    PENDING = "pending", _("pending")
    READY = "ready", _("ready")
    ERROR = "error", _("error")
