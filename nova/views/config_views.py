from django.contrib.auth.decorators import login_required
from django.db import models
from django.shortcuts import redirect, reverse, get_object_or_404
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from nova.models.models import Agent, UserProfile, LLMProvider
from ..forms import AgentForm, LLMProviderForm


@csrf_protect
@login_required
def create_agent(request):
    if request.method == "POST":
        form = AgentForm(request.POST, user=request.user)
        if form.is_valid():
            agent = form.save(commit=False)
            agent.user = request.user
            agent.save()
            # Many-to-Many : tools & agent_tools
            form.save_m2m()
            return redirect(reverse('user_config') + '?tab=agents')
        # Invalid form : store errors
        request.session['agent_errors'] = form.errors.as_json()
        return redirect(reverse('user_config') + '?tab=agents&error=1')

    # Invalid request
    return redirect(reverse('user_config') + '?tab=agents')


@csrf_protect
@login_required
def edit_agent(request, agent_id):
    agent = get_object_or_404(Agent, pk=agent_id, user=request.user)

    if request.method == "POST":
        form = AgentForm(request.POST, instance=agent, user=request.user)
        if form.is_valid():
            form.save()
            return redirect(reverse('user_config') + '?tab=agents')

        request.session['agent_errors'] = form.errors.as_json()
        return redirect(reverse('user_config') + '?tab=agents&error=1')

    return redirect(reverse('user_config') + '?tab=agents')


@csrf_protect
@login_required
@require_POST
def delete_agent(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id, user=request.user)
    if agent:
        # Delete the agent
        agent.delete()
    return redirect(reverse('user_config') + '?tab=agents')


@csrf_protect
@login_required
def make_default_agent(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id, user=request.user)
    if agent:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.default_agent = agent
        profile.save()
    return redirect(reverse('user_config') + '?tab=agents')


@csrf_protect
@login_required
def create_provider(request):
    if request.method == 'POST':
        form = LLMProviderForm(request.POST)
        if form.is_valid():
            provider = form.save(commit=False)
            provider.user = request.user
            form.save()
            return redirect(reverse('user_config') + '?tab=providers')
        request.session['provider_errors'] = form.errors.as_json()
        return redirect(reverse('user_config') + '?tab=providers&error=1')
    return redirect(reverse('user_config') + '?tab=providers')


@csrf_protect
@login_required
def edit_provider(request, provider_id):
    provider = get_object_or_404(LLMProvider, models.Q(user=request.user) | models.Q(user__isnull=True),
                                 id=provider_id)

    # If the provider is a system one, don't allow edit
    if provider.user is None:
        request.session['provider_errors'] = 'Cannot modify a system provider'
        return redirect(reverse('user_config') + '?tab=providers&error=1')

    if request.method == 'POST':
        form = LLMProviderForm(request.POST, instance=provider)
        if form.is_valid():
            form.save()
            return redirect(reverse('user_config') + '?tab=providers')
        request.session['provider_errors'] = form.errors.as_json()
        return redirect(reverse('user_config') + '?tab=providers&error=1')
    return redirect(reverse('user_config') + '?tab=providers')


@csrf_protect
@login_required
def delete_provider(request, provider_id):
    provider = get_object_or_404(LLMProvider, models.Q(user=request.user) | models.Q(user__isnull=True),
                                 id=provider_id)

    # If the provider is a system one, don't allow deletion
    if provider.user is None:
        request.session['provider_errors'] = 'Cannot delete a system provider'
        return redirect(reverse('user_config') + '?tab=providers&error=1')

    # Check if provider is used by any agents
    if provider.agents.exists():
        # Delete all agents using this provider
        provider.agents.all().delete()

    # Delete the provider
    provider.delete()

    return redirect(reverse('user_config') + '?tab=providers')
