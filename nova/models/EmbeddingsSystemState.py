from django.db import models


class EmbeddingsSystemState(models.Model):
    """Singleton-like state used to track lazy system embeddings backfills."""

    singleton_key = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    provider_available = models.BooleanField(default=False)
    current_fingerprint = models.CharField(max_length=64, blank=True, default="")
    last_backfill_provider_available = models.BooleanField(default=False)
    last_backfill_fingerprint = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.singleton_key = 1
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return "Embeddings system state"
