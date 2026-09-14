from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse

from nova.models.DaySegment import DaySegment
from nova.models.Message import Message
from nova.models.Thread import Thread


def _snippet(text, query, radius=90):
    text = text or ""
    lower, needle = text.lower(), query.lower()
    position = lower.find(needle)
    if position < 0:
        return text[: radius * 2]
    start = max(0, position - radius)
    end = min(len(text), position + len(query) + radius)
    prefix = "…" if start else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _continuous_message_url(message):
    segment = DaySegment.objects.filter(
        user=message.user, thread=message.thread,
        starts_at_message__created_at__lte=message.created_at,
    ).order_by("-day_label").first()
    day = f"&day={segment.day_label.isoformat()}" if segment else ""
    return f"{reverse('continuous_home')}?message_id={message.id}{day}#message-{message.id}"


@login_required(login_url="login")
def search(request):
    query = request.GET.get("q", "").strip()
    include_archived = request.GET.get("archived") in {"1", "true", "yes"}
    results = []
    if query:
        threads = Thread.objects.filter(user=request.user, mode=Thread.Mode.THREAD)
        if not include_archived:
            threads = threads.filter(archived_at__isnull=True)
        title_matches = threads.filter(subject__icontains=query).order_by("-created_at", "-id")
        for thread in title_matches[:50]:
            results.append({
                "thread": thread,
                "snippet": thread.subject,
                "url": f"{reverse('index')}?thread_id={thread.id}",
                "kind": "title",
            })
        message_matches = Message.objects.filter(
            user=request.user,
            thread__user=request.user,
            thread__mode__in=[Thread.Mode.THREAD, Thread.Mode.CONTINUOUS],
            text__icontains=query,
        ).select_related("thread").order_by("-created_at", "-id")
        if not include_archived:
            message_matches = message_matches.filter(
                Q(thread__mode=Thread.Mode.CONTINUOUS) | Q(thread__archived_at__isnull=True)
            )
        seen = {(item["thread"].id, item["kind"]) for item in results}
        for message in message_matches[:100]:
            key = (message.thread_id, f"message:{message.id}")
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "thread": message.thread,
                "message": message,
                "snippet": _snippet(message.text, query),
                "url": (
                    _continuous_message_url(message)
                    if message.thread.mode == Thread.Mode.CONTINUOUS
                    else f"{reverse('index')}?thread_id={message.thread_id}&message_id={message.id}#message-{message.id}"
                ),
                "kind": "message",
            })
        # Continuous summaries are searchable even when a day has no raw message match.
        summaries = DaySegment.objects.filter(
            user=request.user, thread__mode=Thread.Mode.CONTINUOUS,
            summary_markdown__icontains=query,
        ).select_related("thread").order_by("-day_label")
        for segment in summaries[:50]:
            results.append({
                "thread": segment.thread,
                "snippet": _snippet(segment.summary_markdown, query),
                "url": f"{reverse('continuous_home')}?day={segment.day_label.isoformat()}",
                "kind": "summary",
            })
    page = Paginator(results, 50).get_page(request.GET.get("page"))
    context = {"query": query, "results": page.object_list, "page": page, "include_archived": include_archived}
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({
            "results": [
                {"thread_id": item["thread"].id, "subject": item["thread"].subject,
                 "snippet": item["snippet"], "kind": item["kind"], "url": item["url"]}
                for item in page.object_list
            ],
            "page": page.number,
            "has_next": page.has_next(),
            "has_previous": page.has_previous(),
        })
    return render(request, "nova/search.html", context)
