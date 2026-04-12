from django.conf import settings
from django.db import models
from django.db.models import Q

from .memory_common import MemoryRecordStatus


class MemoryDirectory(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="memory_directories",
    )
    virtual_path = models.CharField(max_length=512)
    status = models.CharField(
        max_length=20,
        choices=MemoryRecordStatus.choices,
        default=MemoryRecordStatus.ACTIVE,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "virtual_path"],
                condition=Q(status=MemoryRecordStatus.ACTIVE),
                name="uniq_mem_dir_u_path_a",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "virtual_path"], name="idx_mem_dir_u_path"),
            models.Index(fields=["user", "status"], name="idx_mem_dir_u_status"),
        ]

    def __str__(self) -> str:
        return f"{self.user_id}:{self.virtual_path}"
