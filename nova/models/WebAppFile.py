# nova/models/WebAppFile.py
import re
from django.db import models
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from nova.models.WebApp import WebApp

_ALLOWED_EXTS = {".html", ".css", ".js"}
_MAX_BYTES_PER_FILE = 200 * 1024  # 200 KB
_PATH_RE = re.compile(r"^[A-Za-z0-9._-]{1,120}$")  # single-level filename, no slashes


def _validate_path(path: str):
    if not _PATH_RE.match(path or ""):
        raise ValidationError(
            _("Invalid file path. Only letters, digits, dots, underscores "
              "and hyphens are allowed (max 120).")
        )
    if "/" in path or "\\" in path:
        raise ValidationError(_("Path must be a single filename without directories."))
    lower = path.lower()
    if not any(lower.endswith(ext) for ext in _ALLOWED_EXTS):
        raise ValidationError(_("Invalid file extension. Allowed: .html, .css, .js"))
    if lower.startswith("."):
        raise ValidationError(_("Hidden files are not allowed."))


def _validate_content_size(content: str):
    size = len((content or "").encode("utf-8"))
    if size > _MAX_BYTES_PER_FILE:
        raise ValidationError(_(f"File is too large: {size} bytes. Max {_MAX_BYTES_PER_FILE} bytes."))


class WebAppFile(models.Model):
    """
    A single static asset belonging to a WebApp. Content is stored as UTF-8 text.
    Constraints are enforced to keep the surface area safe for agent-authored apps.
    """
    webapp = models.ForeignKey(
        WebApp,
        related_name="files",
        on_delete=models.CASCADE,
        verbose_name=_("Web application"),
    )
    path = models.CharField(
        max_length=120,
        verbose_name=_("Path (filename)"),
        help_text=_("Example: index.html, styles.css, app.js"),
    )
    content = models.TextField(
        verbose_name=_("Content"),
        help_text=_("File content (UTF-8). Max 200 KB per file."),
    )

    class Meta:
        verbose_name = _("Web application file")
        verbose_name_plural = _("Web application files")
        unique_together = ("webapp", "path")
        indexes = [
            models.Index(fields=["webapp", "path"]),
        ]

    def clean(self):
        _validate_path(self.path)
        _validate_content_size(self.content)

    def __str__(self) -> str:
        return f"{self.webapp.slug}:{self.path}"
