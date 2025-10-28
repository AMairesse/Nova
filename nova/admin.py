from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from nova.models.models import (
    UserParameters, UserProfile, Agent,
    LLMProvider, Task, UserFile, CheckpointLink,
    UserInfo, Interaction
)
from nova.models.Message import Message
from nova.models.Thread import Thread
from nova.models.Tool import Tool, ToolCredential


admin.site.site_header = "Nova Admin"
admin.site.register(Agent)
admin.site.register(Tool)
admin.site.register(ToolCredential)
admin.site.register(UserInfo)


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
