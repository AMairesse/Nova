from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from nova.models import UserParameters, UserProfile

from .models import Agent, Tool, ToolCredential, Thread, Message, LLMProvider
admin.site.site_header = "Nova Admin"
admin.site.register(Agent)
admin.site.register(Tool)
admin.site.register(ToolCredential)
admin.site.register(Thread)
admin.site.register(Message)
admin.site.register(LLMProvider)

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

admin.site.unregister(User)
admin.site.register(User, UserAdmin)
