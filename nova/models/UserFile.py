# nova/models/UserFile.py
import botocore.config
import boto3
import logging
from botocore.exceptions import ClientError
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

logger = logging.getLogger(__name__)


# Model for user-uploaded files stored in MinIO
class UserFile(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL,
                             on_delete=models.CASCADE,
                             related_name='files')
    thread = models.ForeignKey('Thread', on_delete=models.SET_NULL, null=True,
                               blank=True, related_name='files')
    # S3 object key (e.g., users/user_id/threads/thread_id/dir/subdir/file.txt)
    key = models.CharField(max_length=255, unique=True)
    original_filename = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=100)
    # File size in bytes
    size = models.PositiveIntegerField()
    # Auto-delete after this date
    expiration_date = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = (('user', 'key'),)

    def __str__(self):
        return f"{self.original_filename} ({self.key})"

    def save(self, *args, **kwargs):
        # Generate key only if not set (allow overrides if needed)
        if not self.key and self.user and self.thread:
            self.key = f"users/{self.user.id}/threads/{self.thread.id}{self.original_filename}"

        # Save the object first to set auto_now_add fields
        super().save(*args, **kwargs)

        # Now calculate expiration_date if not already set
        if not self.expiration_date:
            self.expiration_date = self.created_at + timedelta(days=30)
            # Save again with updated field
            super().save(update_fields=['expiration_date'])

    def get_download_url(self, expires_in=3600):
        """Generate presigned URL for download (expires in seconds)."""
        if self.expiration_date and self.expiration_date < timezone.now():
            self.delete()
            raise ValueError("File expired and deleted.")

        # Get external base from trusted origins (includes port like :8080)
        external_base = settings.CSRF_TRUSTED_ORIGINS[0].rstrip('/')

        s3_client = boto3.client(
            's3',
            endpoint_url=settings.MINIO_ENDPOINT_URL,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY,
            config=botocore.config.Config(
                signature_version='s3v4',
            ),
        )
        try:
            # Generate presigned URL
            url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': settings.MINIO_BUCKET_NAME, 'Key': self.key},
                ExpiresIn=expires_in
            )

            # Change the URL to include the external base
            url = url.replace(settings.MINIO_ENDPOINT_URL, external_base)

            return url
        except ClientError as e:
            logger.error(f"Error generating presigned URL: {e}")
            return None

    def delete(self, *args, **kwargs):
        """Delete from DB and MinIO."""
        s3_client = boto3.client(
            's3',
            endpoint_url=settings.MINIO_ENDPOINT_URL,
            aws_access_key_id=settings.MINIO_ACCESS_KEY,
            aws_secret_access_key=settings.MINIO_SECRET_KEY
        )
        try:
            s3_client.delete_object(Bucket=settings.MINIO_BUCKET_NAME,
                                    Key=self.key)
        except ClientError as e:
            logger.error(f"Error deleting from MinIO: {e}")
        super().delete(*args, **kwargs)
