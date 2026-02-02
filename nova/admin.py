from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User

from nova.models.AgentConfig import AgentConfig
from nova.models.CheckpointLink import CheckpointLink
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
admin.site.register(AgentConfig)
admin.site.register(DaySegment)
admin.site.register(MemoryTheme)
admin.site.register(MemoryItem)
admin.site.register(MemoryItemEmbedding)
admin.site.register(ScheduledTask)
admin.site.register(Tool)
admin.site.register(TranscriptChunk)
admin.site.register(ToolCredential)


class FilesInline(admin.TabularInline):
    model = UserFile
    verbose_name_plural = "files"


class CheckpointLinksInline(admin.TabularInline):
    model = CheckpointLink
    verbose_name_plural = "Checkpoint Links"


@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    inlines = [FilesInline, CheckpointLinksInline]


@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider_type', 'model',
                    'user', 'max_context_tokens')
    fields = ('name', 'provider_type', 'model', 'api_key', 'base_url',
              'additional_config', 'max_context_tokens', 'user')


admin.site.register(Task)


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


admin.site.register(Message)
admin.site.unregister(User)
admin.site.register(User, UserAdmin)
admin.site.register(UserFile)
admin.site.register(CheckpointLink)
admin.site.register(Interaction)


class WebAppFilesInline(admin.TabularInline):
    model = WebAppFile
    can_delete = False
    verbose_name_plural = "files"


@admin.register(WebApp)
class WebAppAdmin(admin.ModelAdmin):
    list_display = ('user', 'thread', 'slug', 'created_at', 'updated_at')
    inlines = [WebAppFilesInline]
