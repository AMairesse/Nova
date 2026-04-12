from django.db import models


class TerminalCommandFailureMetric(models.Model):
    bucket_date = models.DateField(db_index=True)
    head_command = models.CharField(max_length=64, db_index=True, blank=True, default="")
    failure_kind = models.CharField(max_length=40, db_index=True)
    count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField()
    recent_examples = models.JSONField(default=list, blank=True)
    last_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["bucket_date", "head_command", "failure_kind"],
                name="uniq_term_fail_metric_bucket",
            ),
        ]
        indexes = [
            models.Index(
                fields=["bucket_date", "failure_kind"],
                name="idx_term_fail_bucket_kind",
            ),
            models.Index(
                fields=["head_command", "failure_kind"],
                name="idx_term_fail_head_kind",
            ),
            models.Index(fields=["last_seen_at"], name="idx_term_fail_seen"),
        ]

    def __str__(self) -> str:
        return (
            f"{self.bucket_date} {self.head_command or '(empty)'} "
            f"{self.failure_kind} x{self.count}"
        )
