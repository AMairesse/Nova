import json
import re
import bleach
from markdown import markdown
from django.core.cache import cache
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_protect
from django.views.decorators.http import require_POST
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from ..models import Actor, Thread, Agent, UserProfile
from ..llm_agent import LLMAgent
from ..utils import extract_final_answer


ALLOWED_TAGS = [
    "p", "strong", "em", "ul", "ol", "li", "code", "pre", "blockquote",
    "br", "hr", "a",
]
ALLOWED_ATTRS = {
    "a": ["href", "title", "rel"],
}


@ensure_csrf_cookie
@login_required(login_url='login')
def index(request):
    # Load all threads for this user.
    threads = Thread.objects.filter(user=request.user).order_by('-created_at')

    return render(request, 'nova/index.html', {
        'threads': threads,
    })

@csrf_protect
@login_required(login_url='login')
def message_list(request):
    """
    Ajax endpoint returning the partial HTML snippet (message_container.html)
    for a given thread.
    """
    user_agents = Agent.objects.filter(user=request.user, is_tool=False)
    
    agent_id = request.GET.get('agent_id')
    default_agent = None
    if agent_id:
        default_agent = Agent.objects.filter(id=agent_id, user=request.user).first()
    if not default_agent:
        default_agent = getattr(request.user.userprofile, "default_agent", None)

    selected_thread_id = request.GET.get('thread_id')
    messages = None
    if selected_thread_id:
        # Check ownership
        selected_thread = get_object_or_404(
            Thread, id=selected_thread_id, user=request.user
        )
        # Get the messages
        messages = selected_thread.get_messages()
        for m in messages:
            raw_html = markdown(m.text, extensions=["extra"])
            clean_html = bleach.clean(
                raw_html,
                tags=ALLOWED_TAGS,
                attributes=ALLOWED_ATTRS,
                strip=True,
            )
            m.rendered_html = mark_safe(clean_html)
    
    return render(request, 'nova/message_container.html', {
        'messages': messages,
        'thread_id': selected_thread_id or '',
        'user_agents': user_agents,
        'default_agent': default_agent
    })

def new_thread(request):
    count = Thread.objects.filter(user=request.user).count() + 1
    thread_subject = f"thread n°{count}"
    thread = Thread.objects.create(subject=thread_subject, user=request.user)

    # Render the thread item template
    thread_html = render_to_string('nova/partials/_thread_item.html', 
                                 {'thread': thread}, 
                                 request=request)

    return thread, thread_html

@require_POST
@login_required(login_url='login')
def create_thread(request):
    thread, thread_html = new_thread(request)

    return JsonResponse({
        "status": "OK",
        'thread_id': thread.id,
        'threadHtml': thread_html
    })

@require_POST
@login_required(login_url='login')
def delete_thread(request, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)
    thread.delete()
    return redirect('index')

@csrf_protect
@require_POST
@login_required(login_url='login')
def add_message(request):
    thread_id = request.POST.get('thread_id')
    new_message = request.POST.get('new_message', '')
    selected_agent = request.POST.get('selected_agent')

    if not thread_id or thread_id == 'None':
        # New thread
        thread, thread_html = new_thread(request)
    else:
        thread       = Thread.objects.get(id=thread_id)
        thread_html  = None

    thread.add_message(new_message, actor=Actor.USER)

    # Return the thread_id to the client because it's needed for SSE
    return JsonResponse({
        "status": "OK",
        "thread_id": thread.id,
        "threadHtml": thread_html
    })


@login_required(login_url='login')
def stream_llm_response(request, thread_id):
    # Retrieve the thread and all associated messages
    thread   = get_object_or_404(Thread, id=thread_id, user=request.user)
    messages = thread.get_messages()

    # ---------- 1) Which agent ----------
    agent_id = request.GET.get('agent_id')
    agent_obj = None
    if agent_id:
        try:
            agent_obj = Agent.objects.get(id=agent_id, user=request.user)
        except Agent.DoesNotExist:
            pass

    if not agent_obj:
        try:
            agent_obj = request.user.userprofile.default_agent
        except UserProfile.DoesNotExist:
            agent_obj = None

    # ---------- 2) Cache ----------
    agent_key = agent_obj.id if agent_obj else 'none'
    llm_key   = f"{request.user.username}-llm-{thread_id}-{agent_key}"
    llm       = cache.get(llm_key)

    if not llm:
        # Get previous messages except the last one
        msg_history = [[m.actor, m.text] for m in messages]
        if msg_history:
            msg_history.pop()
        llm = LLMAgent(
            request.user,
            thread_id,
            msg_history=msg_history,
            agent=agent_obj
        )
        # cache.set(llm_key, llm, 3600)

    def llm_stream():
        last_message = messages.last().text

        # --------- Map events ----------
        from markdown import markdown

        def to_text(raw):
            """
            Garantit une string :
            - str inchangée
            - liste / dict  → json.dumps(default=str)
            - autre objet   → str(obj)
            """
            if isinstance(raw, str):
                return raw
            if isinstance(raw, (list, dict)):
                return json.dumps(raw, ensure_ascii=False, default=str)
            return str(raw)

        def map_event(ev: dict):
            evt      = ev["event"]          # ex: on_chain_start
            depth    = len(ev.get("parent_ids", []))
            name     = ev["name"]
            kind     = "agent" if evt.startswith("on_chain") else "tool"

            if evt.endswith("_start"):
                return {
                    "event": "start",
                    "kind" : kind,
                    "name" : name,
                    "depth": depth
                }

            if evt.endswith("_stream"):
                chunk = ev["data"].get("chunk", "")

                def is_call(obj):
                    if isinstance(obj, str):
                        return bool(re.match(r'^\s*\[\s*{\s*"name"\s*:', obj))
                    if isinstance(obj, dict):
                        return bool(obj.get("tool_calls"))
                    return False

                if is_call(chunk):
                    return None 
                txt   = to_text(chunk)
                if not txt:
                    return None
                return {
                    "event"  : "stream",
                    "kind"   : kind,
                    "name"   : name,
                    "depth"  : depth,
                    "chunk"  : markdown(txt, extensions=['extra'])
                }

            if evt.endswith("_end"):
                out = ev["data"].get("output", "")
                txt = extract_final_answer(out)
                return {
                    "event"  : "end",
                    "kind"   : kind,
                    "name"   : name,
                    "depth"  : depth,
                    "output" : markdown(txt, extensions=['extra'])
                }
            return None

        # --------------- Server-sent events -------------------------------
        final_output = None
        for ev in llm.stream_events(last_message):
            payload = map_event(ev)
            if not payload:
                continue

            # Keep the final output into the database
            if (payload["event"] == "end") and payload["depth"] == 0:
                final_output = payload.get("output", "")
                thread.add_message(final_output.lstrip(), actor=Actor.AGENT)

            yield "data: " + json.dumps(payload) + "\n\n"

        # --- Stream ends ----------------------------------------
        if thread.subject.startswith("thread n°"):
            short_title = llm.invoke(
                "Give me a short title for this conversation (1–3 words maximum)."
                "Use the same language as the conversation."
                "Answer by giving only the title, nothing else."
            )
            thread.subject = short_title.strip()
            thread.save()

        yield "event: close\n"
        yield f"data: {thread.subject}\n\n"

    return StreamingHttpResponse(llm_stream(), content_type='text/event-stream')
