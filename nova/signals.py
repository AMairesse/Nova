# nova/signals.py
from django.conf import settings
from django.db.models.signals import post_save, pre_delete
from django.dispatch import receiver
import logging

from nova.models.models import UserProfile, UserParameters
from nova.models.Thread import Thread
from nova.llm.checkpoints import get_checkpointer_sync

logger = logging.getLogger(__name__)


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile_and_params(sender, instance, created, **kwargs):
    """
    Automatically create UserProfile and UserParameters
    when a new User is created.
    """
    if created:
        UserProfile.objects.create(user=instance)
        UserParameters.objects.create(user=instance)


@receiver(pre_delete, sender=Thread)
def cleanup_thread(sender, instance, **kwargs):
    """
    Delete all files and CheckpointsLink associated with
    a thread before the thread is deleted. This ensures MinIO
    files are properly cleaned up and Langgraph's checkpoints are removed.
    """
    files_to_delete = instance.files.all()

    if files_to_delete.exists():
        file_count = files_to_delete.count()
        logger.info(f"Deleting {file_count} files for thread {instance.id} ('{instance.subject}')")

        deleted_count = 0
        failed_count = 0

        for file_obj in files_to_delete:
            try:
                file_key = file_obj.key
                file_obj.delete()  # This handles both DB and MinIO cleanup
                logger.debug(f"Successfully deleted file {file_key}")
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete file {file_obj.key}: {e}")
                failed_count += 1

        logger.info(f"Thread {instance.id} file cleanup completed: {deleted_count} deleted, {failed_count} failed")
    else:
        logger.debug(f"No files to delete for thread {instance.id}")

    # Get all checkpoints for this thread
    checkpoints_to_delete = instance.checkpoint_links.all()
    checkpointer = get_checkpointer_sync()

    if checkpoints_to_delete.exists():
        checkpoint_count = checkpoints_to_delete.count()
        logger.info(f"Deleting {checkpoint_count} checkpoints for thread {instance.id} ('{instance.subject}')")

        deleted_count = 0
        failed_count = 0

        for checkpoint_obj in checkpoints_to_delete:
            try:
                checkpoint_id = checkpoint_obj.checkpoint_id
                checkpointer.delete_thread(checkpoint_id)
                checkpoint_obj.delete()
                logger.debug(f"Successfully deleted checkpoint {checkpoint_id}")
                deleted_count += 1
            except Exception as e:
                logger.error(f"Failed to delete checkpoint {checkpoint_obj.checkpoint_id}: {e}")
                failed_count += 1

        logger.info(f"Thread {instance.id} checkpoint cleanup completed: {deleted_count} deleted, {failed_count} failed")
    else:
        logger.debug(f"No checkpoints to delete for thread {instance.id}")
