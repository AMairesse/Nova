from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, reverse, get_object_or_404
from django.views.decorators.http import require_POST
from ..models import Agent, UserProfile
from ..forms import AgentForm


@login_required
def create_agent(request):
    if request.method == "POST":
        form = AgentForm(request.POST, user=request.user)
        if form.is_valid():
            agent = form.save(commit=False)
            agent.user = request.user
            agent.save()
            form.save_m2m()                  # Many-to-Many : tools & agent_tools
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
