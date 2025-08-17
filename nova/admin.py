from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from nova.models import UserParameters, UserProfile

from .models import Agent, Tool, ToolCredential, Thread, Message, LLMProvider, Task, UserFile
admin.site.site_header = "Nova Admin"
admin.site.register(Agent)
admin.site.register(Tool)
admin.site.register(ToolCredential)

class FilesInline(admin.TabularInline):
    model = UserFile
    verbose_name_plural = "files"

@admin.register(Thread)
class ThreadAdmin(admin.ModelAdmin):
    inlines = [FilesInline]

@admin.register(LLMProvider)
class LLMProviderAdmin(admin.ModelAdmin):
    list_display = ('name', 'provider_type', 'model', 'user', 'max_context_tokens')  # Ajout de max_context_tokens
    fields = ('name', 'provider_type', 'model', 'api_key', 'base_url', 'additional_config', 'max_context_tokens', 'user')  # Ajout au form admin

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
