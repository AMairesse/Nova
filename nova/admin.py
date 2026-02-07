from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
from nova.models.ConversationEmbedding import DaySegmentEmbedding, TranscriptChunkEmbedding
from nova.models.DaySegment import DaySegment
from nova.models.Interaction import Interaction
from nova.models.Memory import MemoryTheme, MemoryItem, MemoryItemEmbedding
from nova.models.Message import Message
from nova.models.Provider import LLMProvider
from nova.models.ScheduledTask import ScheduledTask
from nova.models.Task import Task
from nova.models.Thread import Thread
from nova.models.Tool import Tool, ToolCredential
from nova.models.TranscriptChunk import TranscriptChunk
from nova.models.UserFile import UserFile
from nova.models.UserObjects import UserParameters, UserProfile
from nova.models.WebApp import WebApp
from nova.models.WebAppFile import WebAppFile


admin.site.site_header = "Nova Admin"


class FilesInline(admin.TabularInline):
    model = UserFile
    verbose_name_plural = "files"
    extra = 0


class DaySegmentsInline(admin.TabularInline):
    model = DaySegment
    verbose_name_plural = "Day Segments"
    extra = 0
    fields = ("day_label", "starts_at_message", "summary_until_message", "updated_at")
    readonly_fields = ("updated_at",)


class TranscriptChunksInline(admin.TabularInline):
    model = TranscriptChunk
    verbose_name_plural = "Transcript Chunks"
    extra = 0
    fields = ("day_segment", "start_message", "end_message", "token_estimate", "updated_at")
    readonly_fields = ("updated_at",)


class CheckpointLinksInline(admin.TabularInline):
    model = CheckpointLink
    verbose_name_plural = "Checkpoint Links"
    extra = 0


class DaySegmentEmbeddingInline(admin.StackedInline):
    model = DaySegmentEmbedding
    extra = 0
    can_delete = False
    readonly_fields = ("provider_type", "model", "dimensions", "state", "error", "created_at", "updated_at")


class TranscriptChunkEmbeddingInline(admin.StackedInline):
    model = TranscriptChunkEmbedding
    extra = 0
    can_delete = False
    readonly_fields = ("provider_type", "model", "dimensions", "state", "error", "created_at", "updated_at")


class MemoryEmbeddingInline(admin.StackedInline):
    model = MemoryItemEmbedding
    extra = 0
    can_delete = False
    readonly_fields = ("provider_type", "model", "dimensions", "state", "error", "created_at", "updated_at")


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    list_display = ("id", "subject", "mode", "user", "created_at")
    list_filter = ("mode", "created_at")
    search_fields = ("subject", "user__username", "user__email")
    ordering = ("-created_at",)
    inlines = [FilesInline, CheckpointLinksInline, DaySegmentsInline, TranscriptChunksInline]


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider_type', 'model',
                    'user', 'max_context_tokens')
    fields = ('name', 'provider_type', 'model', 'api_key', 'base_url',
              'additional_config', 'max_context_tokens', 'user')


@admin.register(AgentConfig)
class AgentConfigAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "is_tool", "llm_provider", "recursion_limit")
    list_filter = ("is_tool", "llm_provider__provider_type")
    search_fields = ("name", "user__username", "user__email")


@admin.register(DaySegment)
class DaySegmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "day_label",
        "user",
        "thread",
        "starts_at_message",
        "summary_until_message",
        "updated_at",
    )
    list_filter = ("day_label", "updated_at")
    search_fields = ("user__username", "thread__subject", "summary_markdown")
    ordering = ("-day_label", "-updated_at")
    readonly_fields = ("created_at", "updated_at")
    inlines = [DaySegmentEmbeddingInline]


@admin.register(TranscriptChunk)
class TranscriptChunkAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "thread",
        "day_segment",
        "start_message",
        "end_message",
        "token_estimate",
        "updated_at",
    )
    list_filter = ("updated_at",)
    search_fields = ("user__username", "thread__subject", "content_hash")
    ordering = ("-updated_at",)
    readonly_fields = ("created_at", "updated_at", "content_hash")
    inlines = [TranscriptChunkEmbeddingInline]


@admin.register(DaySegmentEmbedding)
class DaySegmentEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "day_segment", "state", "provider_type", "model", "dimensions", "updated_at")
    list_filter = ("state", "provider_type", "updated_at")
    search_fields = ("user__username", "day_segment__thread__subject", "model", "error")
    ordering = ("-updated_at",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(TranscriptChunkEmbedding)
class TranscriptChunkEmbeddingAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "transcript_chunk",
        "state",
        "provider_type",
        "model",
        "dimensions",
        "updated_at",
    )
    list_filter = ("state", "provider_type", "updated_at")
    search_fields = ("user__username", "transcript_chunk__thread__subject", "model", "error")
    ordering = ("-updated_at",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(MemoryTheme)
class MemoryThemeAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "slug", "display_name", "updated_at")
    search_fields = ("user__username", "slug", "display_name")
    ordering = ("user", "slug")


@admin.register(MemoryItem)
class MemoryItemAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "theme", "type", "status", "created_at", "updated_at")
    list_filter = ("type", "status", "created_at")
    search_fields = ("user__username", "content")
    ordering = ("-updated_at",)
    inlines = [MemoryEmbeddingInline]


@admin.register(MemoryItemEmbedding)
class MemoryItemEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "item", "state", "provider_type", "model", "dimensions", "updated_at")
    list_filter = ("state", "provider_type", "updated_at")
    search_fields = ("user__username", "item__content", "model", "error")
    ordering = ("-updated_at",)


@admin.register(ScheduledTask)
class ScheduledTaskAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "user", "task_kind", "maintenance_task", "cron_expression", "is_active", "updated_at")
    list_filter = ("task_kind", "is_active", "updated_at")
    search_fields = ("name", "user__username", "maintenance_task")


@admin.register(Tool)
class ToolAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "tool_type", "tool_subtype", "user", "is_active", "updated_at")
    list_filter = ("tool_type", "tool_subtype", "is_active", "updated_at")
    search_fields = ("name", "description", "python_path", "user__username")


@admin.register(ToolCredential)
class ToolCredentialAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "tool", "auth_type", "updated_at")
    list_filter = ("auth_type", "updated_at")
    search_fields = ("user__username", "tool__name")


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "thread", "agent_config", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("user__username", "thread__subject", "agent_config__name")
    ordering = ("-created_at",)


class UserParametersInline(admin.StackedInline):
    model = UserParameters
    can_delete = False
    verbose_name_plural = "parameters"


class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "profile"


class UserAdmin(BaseUserAdmin):
    inlines = [UserParametersInline, UserProfileInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "thread", "actor", "message_type", "created_at")
    list_filter = ("actor", "message_type", "created_at")
    search_fields = ("user__username", "thread__subject", "text")
    ordering = ("-created_at",)


admin.site.unregister(User)
admin.site.register(User, UserAdmin)


@admin.register(UserFile)
class UserFileAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "thread", "original_filename", "size", "expiration_date")
    list_filter = ("mime_type", "expiration_date")
    search_fields = ("user__username", "original_filename", "key")


@admin.register(CheckpointLink)
class CheckpointLinkAdmin(admin.ModelAdmin):
    list_display = (
        "checkpoint_id",
        "thread",
        "agent",
        "continuous_context_built_at",
    )
    list_filter = ("continuous_context_built_at",)
    search_fields = ("thread__subject", "agent__name")


@admin.register(Interaction)
class InteractionAdmin(admin.ModelAdmin):
    list_display = ("id", "task", "thread", "agent_config", "status", "updated_at")
    list_filter = ("status", "updated_at")
    search_fields = ("question", "origin_name", "thread__subject")


class WebAppFilesInline(admin.TabularInline):
    model = WebAppFile
    can_delete = False
    verbose_name_plural = "files"


@admin.register(WebApp)
class WebAppAdmin(admin.ModelAdmin):
    list_display = ('user', 'thread', 'slug', 'created_at', 'updated_at')
    inlines = [WebAppFilesInline]
