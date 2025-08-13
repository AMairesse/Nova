# nova/file_utils.py
import datetime as dt
import base64
from django.conf import settings
import boto3
from botocore.exceptions import ClientError
import logging

from .models import UserFile

logger = logging.getLogger(__name__)

def extract_file_content(user_file: UserFile) -> str:
    """Extract text or base64 from file in MinIO. Returns str (text or base64)."""
    if user_file.expiration_date < dt.datetime.now(dt.timezone.utc):
        user_file.delete()  # Auto-clean expired
        raise ValueError("File expired and deleted")

    s3_client = boto3.client(
        's3',
        endpoint_url=settings.MINIO_ENDPOINT_URL,
        aws_access_key_id=settings.MINIO_ACCESS_KEY,
        aws_secret_access_key=settings.MINIO_SECRET_KEY
    )
    try:
        obj = s3_client.get_object(Bucket=settings.MINIO_BUCKET_NAME, Key=user_file.key)
        content = obj['Body'].read()
        mime = user_file.mime_type
        if mime.startswith('text/') or mime == 'application/pdf':
            return content.decode('utf-8')  # Text extraction (PDF needs pypdf2 later)
        elif mime.startswith('image/'):
            return base64.b64encode(content).decode('utf-8')  # Base64 for images
        else:
            raise ValueError(f"Unsupported MIME type: {mime}")
    except ClientError as e:
        logger.error(f"Error extracting file {user_file.key}: {e}")
        raise
