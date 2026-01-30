from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from pgvector.django import VectorField


class MemoryTheme(models.Model):
    """User-level grouping for long-term memory items."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_themes",
    )
    slug = models.SlugField(max_length=80)
    display_name = models.CharField(max_length=120)
    description = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "slug"], name="uniq_memory_theme_user_slug"),
        ]
        indexes = [
            models.Index(fields=["user", "slug"], name="idx_memory_theme_user_slug"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.slug}"


class MemoryItemType(models.TextChoices):
    PREFERENCE = "preference", _("preference")
    FACT = "fact", _("fact")
    INSTRUCTION = "instruction", _("instruction")
    SUMMARY = "summary", _("summary")
    OTHER = "other", _("other")


class MemoryItemStatus(models.TextChoices):
    ACTIVE = "active", _("active")
    SUPERSEDED = "superseded", _("superseded")
    ARCHIVED = "archived", _("archived")


class MemoryItem(models.Model):
    """Atomic long-term memory unit, scoped to a user."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_items",
    )
    theme = models.ForeignKey(
        MemoryTheme,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items",
    )

    type = models.CharField(max_length=20, choices=MemoryItemType.choices)
    content = models.TextField()

    source_thread = models.ForeignKey(
        "nova.Thread",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memory_items",
    )
    source_message = models.ForeignKey(
        "nova.Message",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memory_items",
    )

    tags = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=20,
        choices=MemoryItemStatus.choices,
        default=MemoryItemStatus.ACTIVE,
    )
    supersedes = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="superseded_by",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "created_at"], name="idx_memory_item_user_created"),
            # NOTE: index names must be <= 30 chars (Django constraint for some backends)
            models.Index(fields=["user", "theme", "created_at"], name="idx_mem_item_u_t_created"),
            models.Index(fields=["user", "type"], name="idx_memory_item_user_type"),
            models.Index(fields=["user", "status"], name="idx_memory_item_user_status"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.id}"


class MemoryEmbeddingState(models.TextChoices):
    PENDING = "pending", _("pending")
    READY = "ready", _("ready")
    ERROR = "error", _("error")


class MemoryItemEmbedding(models.Model):
    """Embedding vector for a MemoryItem.

    NOTE: the actual pgvector field will be added once the `pgvector` Python dependency
    (and migrations enabling the extension) are in place.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_item_embeddings",
    )
    item = models.OneToOneField(
        MemoryItem,
        on_delete=models.CASCADE,
        related_name="embedding",
    )

    provider_type = models.CharField(max_length=40, blank=True, default="")
    model = models.CharField(max_length=120, blank=True, default="")
    dimensions = models.IntegerField(null=True, blank=True)

    state = models.CharField(
        max_length=20,
        choices=MemoryEmbeddingState.choices,
        default=MemoryEmbeddingState.PENDING,
    )
    error = models.TextField(null=True, blank=True)

    # pgvector column (fixed dimension).
    # Nullable to support FTS-only mode when embeddings provider is not configured.
    vector = VectorField(dimensions=1024, null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "state"], name="idx_memory_embed_user_state"),
        ]
