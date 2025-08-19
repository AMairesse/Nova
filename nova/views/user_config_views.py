# nova/views/user_config_views.py
from django.db import models
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.utils.decorators import method_decorator
from django.views.generic import View
from nova.models.models import UserParameters, Agent, UserProfile, LLMProvider, Tool, ProviderType
from ..forms import UserParametersForm, AgentForm
from nova.tools import get_available_tool_types


@method_decorator(login_required(login_url='login'), name='dispatch')
class UserConfigView(View):
    template_name = 'nova/user_config.html'

    def _get_common_context(self, request, user_params_form=None,
                            active_tab='providers'):
        """
        Prépare le contexte commun pour GET et invalid POST.
        Factorise la logique pour éviter la duplication.
        """
        # Retrieve / create the user's parameters and profile
        user_params, _ = UserParameters.objects.get_or_create(user=request.user)
        user_profile, _ = UserProfile.objects.get_or_create(user=request.user)

        # Bind forms (use provided form if available, else create new)
        if user_params_form is None:
            user_params_form = UserParametersForm(instance=user_params)

        agent_form = AgentForm(user=request.user)

        # Get available tool types
        tool_types = get_available_tool_types()

        # Get agents and tools agents
        agents = Agent.objects.filter(user=request.user)
        agents_normal = agents.filter(is_tool=False)
        agents_tools = agents.filter(is_tool=True)

        return {
            'user_params_form': user_params_form,
            'active_tab': active_tab,
            'user_params': user_params,
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
        }

    def get(self, request, *args, **kwargs):
        active_tab = request.GET.get('tab', 'providers')
        context = self._get_common_context(request, active_tab=active_tab)
        return render(request, self.template_name, context)

    def post(self, request, *args, **kwargs):
        # Retrieve user parameters
        user_params, _ = UserParameters.objects.get_or_create(user=request.user)

        # Build the user parameters form
        user_params_form = UserParametersForm(request.POST, instance=user_params)

        action = request.POST.get('action')

        if action == 'save_settings':
            if user_params_form.is_valid():
                user_params_form.save()
                return redirect('user_config')

            # Invalid: use common context with forced tab and bound form
            context = self._get_common_context(request, user_params_form=user_params_form, active_tab='general-config')
            return render(request, self.template_name, context)

        # Fallback if neither button recognized (rare)
        return redirect('user_config')
