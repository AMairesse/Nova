from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST

from nova.message_tail_service import (
    MessageTailDeletionError,
    build_message_tail_preview,
    delete_message_tail_after,
)
from nova.models.Message import Message
from nova.models.Thread import Thread


@require_GET
@login_required(login_url="login")
def preview_delete_message_tail(request, message_id: int):
    message = get_object_or_404(
        Message.objects.select_related("thread", "user"),
        id=message_id,
        user=request.user,
        thread__user=request.user,
    )
    try:
        preview = build_message_tail_preview(message, request.user)
    except MessageTailDeletionError as exc:
        return JsonResponse({"status": "ERROR", "message": str(exc)}, status=400)

    payload = preview.serialize()
    payload["status"] = "OK"
    return JsonResponse(payload)


@csrf_protect
@require_POST
@login_required(login_url="login")
def delete_message_tail(request, message_id: int):
    message = get_object_or_404(
        Message.objects.select_related("thread", "user"),
        id=message_id,
        user=request.user,
        thread__user=request.user,
    )
    try:
        result = delete_message_tail_after(message, request.user)
    except MessageTailDeletionError as exc:
        return JsonResponse({"status": "ERROR", "message": str(exc)}, status=400)

    payload = result.serialize()
    payload["status"] = "OK"
    if message.thread.mode == Thread.Mode.CONTINUOUS:
        if result.redirect_day:
            payload["redirect_url"] = f"{reverse('continuous_home')}?day={result.redirect_day}"
        else:
            payload["redirect_url"] = reverse("continuous_home")
    return JsonResponse(payload)
