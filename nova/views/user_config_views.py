# nova/views/user_config_views.py
from django.db import models
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import View
from ..models import UserParameters, Agent, UserProfile, LLMProvider, Tool, ProviderType
from ..forms import UserParametersForm, AgentForm
from nova.tools import get_available_tool_types


@method_decorator(login_required(login_url='login'), name='dispatch')
class UserConfigView(View):
    template_name = 'nova/user_config.html'

    def get(self, request, *args, **kwargs):
        # Retrieve / create the user’s parameters
        user_params, _ = UserParameters.objects.get_or_create(user=request.user)
        
        # Retrieve / create the user's profile
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

        # Bind forms
        user_params_form = UserParametersForm(instance=user_params)

        # Get availables tool types
        tool_types = get_available_tool_types()
        
        agent_form = AgentForm(user=request.user)
        
        # Get agents and tools agents
        agents = Agent.objects.filter(user=request.user)
        agents_normal = agents.filter(is_tool=False)
        agents_tools = agents.filter(is_tool=True)
        
        return render(request, self.template_name, {
            'user_params_form': user_params_form,
            'user_params': user_params,  # For template rendering
            'agent_form': agent_form,
            'agents': agents,
            'agents_normal': agents_normal,
            'agents_tools': agents_tools, 
            'llm_providers': LLMProvider.objects.filter(user=request.user),
            'tools': Tool.objects.filter(
                models.Q(user=request.user) | models.Q(user__isnull=True),
                is_active=True
            ).distinct(),
            'tool_types': tool_types,
            'user_profile': user_profile,
            "PROVIDER_CHOICES": ProviderType.choices,
        })

    def post(self, request, *args, **kwargs):
        # Retrieve user parameters
        user_params, _ = UserParameters.objects.get_or_create(user=request.user)

        # Build the user parameters form
        user_params_form = UserParametersForm(request.POST, instance=user_params)

        action = request.POST.get('action')

        if action == 'save_settings':
            # Now we do the full save
            if user_params_form.is_valid():
                user_params_form.save()

                return redirect('user_config')

            # Load all available tools
            from nova.tools import get_available_tool_types
            tool_types = get_available_tool_types()

            # If invalid, re-render with errors
            return render(request, self.template_name, {
                'user_params_form': user_params_form,
                'agents': Agent.objects.filter(user=request.user),
                'llm_providers': LLMProvider.objects.filter(user=request.user),
                'tools': Tool.objects.filter(
                    models.Q(user=request.user) | models.Q(user__isnull=True),
                    is_active=True
                ).distinct(),
                'agents_tools': Agent.objects.filter(user=request.user, is_tool=True),
                'tool_types': tool_types,
                'user_profile': request.user.userprofile if hasattr(request.user, 'userprofile') else None
            })

        # Fallback if neither button recognized (rare)
        return redirect('user_config')
