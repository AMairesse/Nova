from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, reverse, get_object_or_404
from django.views.decorators.http import require_POST
from ..models import Agent, UserProfile, LLMProvider
from ..forms import AgentForm

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


@login_required
@require_POST
def delete_agent(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id, user=request.user)
    if agent:
        # Delete the agent
        agent.delete()
    return redirect(reverse('user_config') + '?tab=agents')

@login_required
def make_default_agent(request, agent_id):
    agent = get_object_or_404(Agent, id=agent_id, user=request.user)
    if agent:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        profile.default_agent = agent
        profile.save()
    return redirect(reverse('user_config') + '?tab=agents')

@login_required
def create_provider(request):
    if request.method == 'POST':
        api_key = request.POST.get('api_key', '').strip() or None
        LLMProvider.objects.create(
            user=request.user,
            name=request.POST['name'],
            provider_type=request.POST['provider_type'],
            model=request.POST.get('model', '').strip(),
            api_key=api_key,
            base_url=request.POST.get('base_url', '').strip() or None,
        )
    return redirect(reverse('user_config') + '?tab=providers')

@login_required
def edit_provider(request, provider_id):
    provider = get_object_or_404(LLMProvider, id=provider_id, user=request.user)

    if request.method == 'POST':
        # Update provider details
        provider.name = request.POST['name']
        provider.provider_type = request.POST['provider_type']
        
        # Update model if provided
        model = request.POST.get('model', '').strip()
        if model:
            provider.model = model
            
        # Update API key if provided
        api_key = request.POST.get('api_key', '').strip()
        if api_key:
            provider.api_key = api_key
            
        # Update base_url if provided
        base_url = request.POST.get('base_url', '').strip()
        if base_url:
            provider.base_url = base_url
        elif 'base_url' in request.POST:  # Field exists but is empty
            provider.base_url = None
            
        # Save changes
        provider.save()

    return redirect(reverse('user_config') + '?tab=providers')

@login_required
def delete_provider(request, provider_id):
    provider = get_object_or_404(LLMProvider, id=provider_id, user=request.user)
    
    # Check if provider is used by any agents
    if provider.agents.exists():
        # Delete all agents using this provider
        provider.agents.all().delete()
    
    # Delete the provider
    provider.delete()
    
    return redirect(reverse('user_config') + '?tab=providers')
