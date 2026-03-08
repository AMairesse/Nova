# nova/signals.py
import logging

from django.conf import settings
from django.db.models.signals import post_delete, post_save, pre_delete
from django.dispatch import receiver
from asgiref.sync import async_to_sync

from nova.llm.checkpoints import get_checkpointer
from nova.models.TaskDefinition import TaskDefinition
from nova.models.UserFile import UserFile
from nova.models.UserObjects import UserParameters, UserProfile
from nova.models.Thread import Thread

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------
@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile_and_params(sender, instance, created, **kwargs):
    """
    Automatically create UserProfile and UserParameters
    when a new User is created.
    """
    if created:
        UserProfile.objects.create(user=instance)
        UserParameters.objects.create(user=instance)


# --------------------------------------------------------------------------
@receiver(post_delete, sender=TaskDefinition)
def cleanup_task_definition_periodic_task(sender, instance: TaskDefinition, **kwargs):
    """
    Remove the matching django-celery-beat schedule even when TaskDefinition is
    deleted via queryset/bulk/cascade paths that bypass model.delete().
    """
    deleted = TaskDefinition.cleanup_periodic_task_for_id(instance.id)
    if deleted:
        logger.info("Deleted %s periodic task(s) for task definition %s", deleted, instance.id)


# --------------------------------------------------------------------------
@receiver(pre_delete, sender=Thread)
def cleanup_thread(sender, instance: Thread, **kwargs):
    """
    Delete all files and CheckpointsLink associated with
    a thread before the thread is deleted. This ensures MinIO
    files are properly cleaned up and Langgraph's checkpoints are removed.
    """
    # ---------- 1. Minio cleanup ------------------------------
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

    # ---------- 2. Delete checkpoints ---------------------------
    checkpoint_links = list(instance.checkpoint_links.all())
    if not checkpoint_links:
        logger.debug("No checkpoints to delete for thread %s", instance.id)
        return

    logger.info(
        "Deleting %s checkpoints for thread %s ('%s')",
        len(checkpoint_links),
        instance.id,
        instance.subject,
    )

    deleted, failed = async_to_sync(_delete_checkpoints_async)(checkpoint_links)

    for cp in deleted:
        cp.delete()

    logger.info(
        "Thread %s checkpoint cleanup completed: %s deleted, %s failed",
        instance.id,
        len(deleted),
        len(failed),
    )
    for cp, err in failed:
        logger.error("Failed to delete checkpoint %s: %s", cp.checkpoint_id, err)


@receiver(pre_delete, sender=UserFile)
def cleanup_userfile_storage(sender, instance: UserFile, **kwargs):
    """Ensure MinIO cleanup also happens on cascade/queryset deletion paths."""
    try:
        instance.delete_storage_object()
    except Exception as exc:
        logger.error("Failed to delete storage object for file %s: %s", instance.pk, exc)


# --------------------------------------------------------------------------
async def _delete_checkpoints_async(checkpoint_links):
    saver = await get_checkpointer()
    try:
        deleted, failed = [], []

        for cp in checkpoint_links:
            try:
                await saver.adelete_thread(cp.checkpoint_id)
                deleted.append(cp)
            except Exception as exc:
                failed.append((cp, str(exc)))

        return deleted, failed
    finally:
        await saver.conn.close()
