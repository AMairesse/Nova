# nova/models/AgentConfig.py
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from nova.models.UserObjects import UserProfile


class AgentConfig(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='user_agents',
                             verbose_name=_("User agents"))
    name = models.CharField(max_length=120)
    llm_provider = models.ForeignKey('LLMProvider',
                                     on_delete=models.CASCADE,
                                     related_name='AgentsConfig',
                                     verbose_name=_("Provider"))
    system_prompt = models.TextField(verbose_name=_("Prompt"))
    recursion_limit = models.IntegerField(default=25, verbose_name=_("Recursion limit"))

    # Tools
    tools = models.ManyToManyField('Tool', blank=True, related_name="agents",
                                   verbose_name=_("Tools"))
    is_tool = models.BooleanField(
        default=False,
        verbose_name=_("Is tool"),
        help_text=_("If true, this agent can be used as a tool by other agents.")
    )

    # Agents as tools
    agent_tools = models.ManyToManyField(
        'self',
        blank=True,
        symmetrical=False,
        related_name='used_by_agents',
        verbose_name=_("Agents to use as tools"),
        limit_choices_to={'is_tool': True}
    )

    # New field for tool description
    tool_description = models.TextField(
        blank=True,
        null=True,
        verbose_name=_("Tool description"),
        help_text=_("Description of this agent when used as a tool (required if is_tool=True)")
    )

    # Summarization settings
    auto_summarize = models.BooleanField(
        default=False,
        help_text="Enable automatic summarization when token threshold is reached"
    )
    token_threshold = models.IntegerField(
        default=100,
        help_text="Token count threshold for triggering summarization"
    )
    preserve_recent = models.IntegerField(
        default=2,
        help_text="Number of recent messages to preserve"
    )
    strategy = models.CharField(
        default='conversation',
        max_length=20,
        help_text="Summarization strategy: conversation, topic, temporal, hybrid"
    )
    max_summary_length = models.IntegerField(
        default=500,
        help_text="Maximum length of generated summary in words"
    )
    summary_model = models.CharField(
        blank=True,
        null=True,
        max_length=100,
        help_text="Optional LLM model override for summarization"
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ----- helpers ---------------------------------------------------------
    class Meta:
        unique_together = (("user", "name"),)

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.is_tool and not self.tool_description:
            raise ValidationError(
                _("Tool description is required when 'is_tool' is True.")
            )

        # Skip cycle-detection on instances without PK
        if self._state.adding:        # object not yet in DB
            return

        # Check for cycles
        if self._has_cycle():
            raise ValidationError(_("Found a cyclic dependency between agents."))

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # Auto-set as default if first normal agent (not tool)
        if not self.is_tool:
            profile, _ = UserProfile.objects.get_or_create(user=self.user)
            if not profile.default_agent:
                profile.default_agent = self
                profile.save()

        # If agent is a tool, remove from default if it was previously default
        if self.is_tool:
            profile, _ = UserProfile.objects.get_or_create(user=self.user)
            if profile.default_agent == self:
                profile.default_agent = None
                profile.save()

    # -----------------------------------------------------------------
    # Internal cycle detector (DFS with recursion stack)
    # -----------------------------------------------------------------
    def _has_cycle(self, visited=None, stack=None):
        """
        Return True if a dependency cycle is found starting from `self`.
        """
        visited = visited or set()
        stack = stack or set()

        if self in stack:          # back-edge ==> cycle
            return True
        if self in visited:        # already explored, no cycle via this node
            return False

        visited.add(self)
        stack.add(self)

        for dep in self.agent_tools.all():
            if dep._has_cycle(visited, stack):
                return True

        stack.remove(self)
        return False
