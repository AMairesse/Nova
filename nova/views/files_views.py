import asyncio
from channels.layers import get_channel_layer
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, HttpResponseBadRequest
from django.shortcuts import render
from django.views import View
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.core.exceptions import PermissionDenied
from asgiref.sync import sync_to_async
import logging

from ..models import UserFile, Thread
from ..file_utils import build_virtual_tree, upload_file_to_minio, auto_rename_path, detect_mime, batch_upload_files

logger = logging.getLogger(__name__)

def sidebar_panel_view(request):
    return render(request, 'nova/files/sidebar_panel.html')

@csrf_protect
@login_required(login_url='login')
def file_list(request, thread_id):
    thread = Thread.objects.filter(id=thread_id, user=request.user).first()
    if not thread:
        logger.error(f"Access denied: Thread {thread_id} not found or unauthorized")
        return JsonResponse({'error': 'Thread not found or unauthorized'}, status=403)
    
    files = UserFile.objects.filter(thread=thread).order_by('original_filename')
    tree = build_virtual_tree(files)
    return JsonResponse({'files': tree}, status=200)

@csrf_protect
@require_POST
@login_required(login_url='login')
async def file_upload(request, thread_id):
    try:
        thread = await sync_to_async(Thread.objects.get)(id=thread_id, user=request.user)
        if not thread:
            return JsonResponse({'error': 'Thread not found or unauthorized'}, status=403)
        
        if 'files' in request.FILES:
            file_data = []
            paths = request.POST.getlist('paths')
            files_list = request.FILES.getlist('files')
            total_files = len(files_list)
            channel_layer = get_channel_layer()
            
            for i, file in enumerate(files_list):
                # Broadcast progress
                progress = int((i / total_files) * 100)
                await channel_layer.group_send(
                    f"thread_{thread_id}_files",
                    {"type": "file_progress", "progress": progress}
                )
                
                content = file.read()
                proposed_path = paths[i] if i < len(paths) else f"/{file.name}"
                file_data.append({'path': proposed_path, 'content': content})
            
            created = await sync_to_async(batch_upload_files)(thread, request.user, file_data)
            
            # Final progress: 100%
            await channel_layer.group_send(
                f"thread_{thread_id}_files",
                {"type": "file_progress", "progress": 100}
            )
            
            return JsonResponse({'success': True, 'files': created})
        return HttpResponseBadRequest({'error': 'No files provided'})
    except Exception as e:
        logger.exception(f"Error in FileUploadView: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

class FileDeleteView(LoginRequiredMixin, View):
    def delete(self, request, file_id):
        file = UserFile.objects.filter(id=file_id, user=request.user).first()
        if not file:
            logger.error(f"Access denied: File {file_id} not found or unauthorized")
            return JsonResponse({'error': 'File not found or unauthorized'}, status=403)
        
        file.delete()  # Uses model's delete for MinIO/DB
        return JsonResponse({'success': True})

class FileMoveView(LoginRequiredMixin, View):
    def patch(self, request, file_id):
        file = UserFile.objects.filter(id=file_id, user=request.user).first()
        if not file:
            return JsonResponse({'error': 'File not found or unauthorized'}, status=403)
        
        new_path = request.POST.get('new_path')
        if not new_path:
            return JsonResponse({'error': 'New path required'}, status=400)
        
        renamed_path = auto_rename_path(file.thread, new_path)  # Auto-rename if duplicate at new location
        file.original_filename = renamed_path
        file.save()  # Updates key via model's save()
        
        return JsonResponse({'success': True, 'new_path': renamed_path})
