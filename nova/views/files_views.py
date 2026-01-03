import asyncio
from channels.layers import get_channel_layer
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render, get_object_or_404
from django.views import View
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_protect
from asgiref.sync import sync_to_async
import logging

from nova.models.Thread import Thread
from nova.models.UserFile import UserFile
from nova.file_utils import (
    build_virtual_tree,
    batch_upload_files,
    MAX_FILE_SIZE
)

logger = logging.getLogger(__name__)


@login_required(login_url='login')
def sidebar_panel_view(request):
    return render(request, 'nova/files/sidebar_panel.html')


@csrf_protect
@login_required(login_url='login')
def file_list(request, thread_id):
    thread = Thread.objects.filter(id=thread_id, user=request.user).first()
    if not thread:
        logger.error(f"Access denied: Thread {thread_id} not found or unauthorized")
        return JsonResponse({'error': 'Thread not found or unauthorized'},
                            status=403)

    files = UserFile.objects.filter(thread=thread).order_by('original_filename')
    tree = build_virtual_tree(files)
    return JsonResponse({'files': tree}, status=200)


async def async_read_file(file) -> bytes:
    """Async-safe file reading with chunking to avoid RAM overload."""
    @sync_to_async
    def sync_read():
        content = b''
        for chunk in file.chunks():  # Use Django's chunked reading
            content += chunk
        return content
    return await sync_read()


@csrf_protect
@require_GET
@login_required(login_url='login')
def file_download_url(request, file_id):
    file = get_object_or_404(UserFile, id=file_id, user=request.user)
    if file.thread.user != request.user:  # Extra ownership check
        return JsonResponse({'error': 'Unauthorized'}, status=403)

    try:
        url = file.get_download_url(expires_in=3600)  # 1-hour expiry
    except ValueError as e:
        # Expected case when retention/expiration is enabled and the file is expired.
        # Use 410 Gone rather than 500.
        return JsonResponse({'error': str(e)}, status=410)

    if not url:
        return JsonResponse({'error': 'Failed to generate URL'}, status=500)

    return JsonResponse({'url': url})


@csrf_protect
@require_POST
@login_required(login_url='login')
async def file_upload(request, thread_id):
    try:
        thread = await sync_to_async(Thread.objects.get)(id=thread_id,
                                                         user=request.user)
        if not thread:
            return JsonResponse({'error': 'Thread not found or unauthorized'},
                                status=403)

        if 'files' not in request.FILES:
            return HttpResponseBadRequest({'error': 'No files provided'})

        paths = request.POST.getlist('paths')
        files_list = request.FILES.getlist('files')
        total_files = len(files_list)
        channel_layer = get_channel_layer()
        file_data = []

        # Async loop with gather for parallel progress sends
        async def process_file(i, file, path):
            logger.debug(f"Processing file {i+1}/{total_files}")
            progress = int(((i + 1) / total_files) * 100)
            await channel_layer.group_send(
                f"thread_{thread_id}_files",
                {"type": "file_progress", "progress": progress}
            )
            content = await async_read_file(file)
            return {'path': path, 'content': content}

        # Gather tasks for all files
        tasks = []
        for i, file in enumerate(files_list):
            proposed_path = paths[i] if i < len(paths) else f"/{file.name}"
            if file.size > MAX_FILE_SIZE:
                return JsonResponse({'success': False,
                                     'error': f'File too large: {file.name}'}, status=400)
            tasks.append(process_file(i, file, proposed_path))

        file_data = await asyncio.gather(*tasks)  # Run in parallel

        created, errors = await batch_upload_files(thread, request.user,
                                                   file_data)

        # Final progress: 100% (redundant but ensures)
        await channel_layer.group_send(
            f"thread_{thread_id}_files",
            {"type": "file_progress", "progress": 100}
        )

        if errors:
            return JsonResponse({'success': False, 'errors': errors},
                                status=400)
        return JsonResponse({'success': True, 'files': created})
    except Exception as e:
        logger.exception(f"Error in FileUploadView: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)


class FileDeleteView(LoginRequiredMixin, View):
    def delete(self, request, file_id):
        file = UserFile.objects.filter(id=file_id, user=request.user).first()
        if not file:
            logger.error(f"Access denied: File {file_id} not found or unauthorized")
            return JsonResponse({'error': 'File not found or unauthorized'},
                                status=403)

        file.delete()  # Uses model's delete for MinIO/DB
        return JsonResponse({'success': True})
