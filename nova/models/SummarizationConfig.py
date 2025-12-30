# nova/models/SummarizationConfig.py
from django.db import models
from django.utils.translation import gettext_lazy as _

from nova.models.AgentConfig import AgentConfig


class SummarizationConfig(models.Model):
    agent = models.OneToOneField(
        AgentConfig,
        on_delete=models.CASCADE,
        related_name='summarization_config',
        verbose_name=_("Agent")
    )
    auto_summarize = models.BooleanField(
        default=True,
        verbose_name=_("Auto-summarize"),
        help_text=_("Enable automatic conversation summarization")
    )
    token_threshold = models.IntegerField(
        default=3000,
        verbose_name=_("Token threshold"),
        help_text=_("Trigger summarization when context exceeds this token count")
    )
    summary_model = models.CharField(
        max_length=100,
        blank=True,
        verbose_name=_("Summary model"),
        help_text=_("Optional model override for summarization (leave blank to use agent model)")
    )
    preserve_recent = models.IntegerField(
        default=5,
        verbose_name=_("Preserve recent messages"),
        help_text=_("Number of recent messages to keep unsummarized")
    )
    strategy = models.CharField(
        max_length=50,
        default='conversation',
        choices=[
            ('conversation', _('Conversation Summary')),
            ('topic', _('Topic-based Summary')),
            ('temporal', _('Temporal Summary')),
            ('hybrid', _('Hybrid Summary')),
        ],
        verbose_name=_("Summarization strategy"),
        help_text=_("Strategy for summarizing conversations")
    )
    max_summary_length = models.IntegerField(
        default=1000,
        verbose_name=_("Max summary length"),
        help_text=_("Maximum length of summary in words")
    )
    compression_ratio = models.FloatField(
        default=0.3,
        verbose_name=_("Compression ratio"),
        help_text=_("Target size reduction (0.3 = 30% of original)")
    )

    class Meta:
        verbose_name = _("Summarization Configuration")
        verbose_name_plural = _("Summarization Configurations")

    def __str__(self):
        return f"Summarization config for {self.agent.name}"