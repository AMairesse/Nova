# nova/views/task_views.py
from copy import deepcopy
import posixpath

from django.urls import reverse
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from nova.models.Task import Task, TaskStatus
from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.message_attachments import build_message_attachment_inbox_paths
from nova.tasks.runtime_state import reconcile_stale_running_tasks

TRACE_FILE_META_KEYS = {
    "output_path",
    "output_paths",
    "output_paths_copied_back",
    "input_paths",
    "input_paths_requested",
    "input_paths_copied",
    "input_file_paths",
    "removed_paths",
}


def _coerce_paths(value):
    if isinstance(value, str):
        candidate = str(value).strip()
        return [candidate] if candidate.startswith("/") else []
    if isinstance(value, (list, tuple, set)):
        paths = []
        for item in value:
            paths.extend(_coerce_paths(item))
        return paths
    return []


def _collect_trace_paths(trace):
    paths = set()
    summary = trace.get("summary") if isinstance(trace, dict) else {}
    if isinstance(summary, dict):
        paths.update(_coerce_paths(summary.get("output_paths")))

    def _walk(node):
        if not isinstance(node, dict):
            return
        meta = node.get("meta")
        if isinstance(meta, dict):
            for key in TRACE_FILE_META_KEYS:
                paths.update(_coerce_paths(meta.get(key)))
        for child in list(node.get("children") or []):
            _walk(child)

    _walk((trace or {}).get("root"))
    return sorted(paths)


def _resolve_trace_file_refs(*, user, thread, paths, source_message_id=None):
    if not paths:
        return {}

    resolved = {}
    thread_files = UserFile.objects.filter(
        user=user,
        thread=thread,
        scope=UserFile.Scope.THREAD_SHARED,
        original_filename__in=paths,
    )
    for user_file in thread_files:
        path = str(user_file.original_filename or "").strip()
        if not path:
            continue
        resolved[path] = {
            "path": path,
            "label": posixpath.basename(path) or path,
            "content_url": reverse("file_content", args=[user_file.id]),
            "mime_type": str(user_file.mime_type or "").strip(),
            "is_image": str(user_file.mime_type or "").strip().lower().startswith("image/"),
        }

    if source_message_id is not None:
        inbox_files = list(
            UserFile.objects.filter(
                user=user,
                thread=thread,
                scope=UserFile.Scope.MESSAGE_ATTACHMENT,
                source_message_id=source_message_id,
            )
        )
        aliases = build_message_attachment_inbox_paths(inbox_files)
        for user_file in inbox_files:
            alias = aliases.get(user_file.id)
            if not alias or alias not in paths:
                continue
            resolved[alias] = {
                "path": alias,
                "label": posixpath.basename(alias) or alias,
                "content_url": reverse("file_content", args=[user_file.id]),
                "mime_type": str(user_file.mime_type or "").strip(),
                "is_image": str(user_file.mime_type or "").strip().lower().startswith("image/"),
            }
    return resolved


def _enrich_execution_trace(trace, *, user, thread):
    if not isinstance(trace, dict):
        return {}

    enriched = deepcopy(trace)
    root = enriched.get("root") if isinstance(enriched.get("root"), dict) else {}
    root_meta = root.get("meta") if isinstance(root.get("meta"), dict) else {}
    source_message_id = root_meta.get("source_message_id")
    refs = _resolve_trace_file_refs(
        user=user,
        thread=thread,
        paths=_collect_trace_paths(enriched),
        source_message_id=source_message_id,
    )

    summary = enriched.get("summary") if isinstance(enriched.get("summary"), dict) else {}
    if summary:
        summary["status"] = str(summary.get("status") or root.get("status") or "").strip() or "unknown"
        summary["resolved_output_files"] = [
            refs[path]
            for path in _coerce_paths(summary.get("output_paths"))
            if path in refs
        ]
        enriched["summary"] = summary

    def _walk(node):
        if not isinstance(node, dict):
            return
        meta = node.get("meta") if isinstance(node.get("meta"), dict) else {}
        linked = []
        seen = set()
        for key in TRACE_FILE_META_KEYS:
            for path in _coerce_paths(meta.get(key)):
                if path in refs and path not in seen:
                    linked.append(refs[path])
                    seen.add(path)
        if linked:
            node["resolved_files"] = linked
        for child in list(node.get("children") or []):
            _walk(child)

    _walk(root)
    return enriched


@login_required
def running_tasks(request, thread_id):
    thread = get_object_or_404(Thread, id=thread_id, user=request.user)

    reconcile_stale_running_tasks(thread=thread, user=request.user)

    running_tasks = Task.objects.filter(
        thread=thread,
        user=request.user,
        status=TaskStatus.RUNNING,
    ).values('id', 'status', 'current_response', 'progress_logs')

    tasks_data = []
    for task in running_tasks:
        tasks_data.append({
            'id': task['id'],
            'status': task['status'],
            'current_response': task['current_response'],
            'last_progress': task['progress_logs'][-1] if task['progress_logs'] else None
        })

    return JsonResponse({'running_tasks': tasks_data})


@login_required
def execution_trace(request, task_id):
    task = get_object_or_404(
        Task.objects.only("id", "status", "thread_id", "execution_trace"),
        id=task_id,
        user=request.user,
    )
    return JsonResponse(
        {
            "status": "OK",
            "task_status": task.status,
            "execution_trace": _enrich_execution_trace(
                task.execution_trace if isinstance(task.execution_trace, dict) else {},
                user=request.user,
                thread=task.thread,
            ),
        }
    )
