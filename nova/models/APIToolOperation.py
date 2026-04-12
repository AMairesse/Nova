from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from nova.models.Tool import Tool


class APIToolOperation(models.Model):
    class HTTPMethod(models.TextChoices):
        GET = "GET", "GET"
        POST = "POST", "POST"
        PUT = "PUT", "PUT"
        PATCH = "PATCH", "PATCH"
        DELETE = "DELETE", "DELETE"

    tool = models.ForeignKey(
        Tool,
        on_delete=models.CASCADE,
        related_name="api_operations",
        verbose_name=_("API tool"),
    )
    name = models.CharField(max_length=120)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True, default="")
    http_method = models.CharField(
        max_length=10,
        choices=HTTPMethod.choices,
        default=HTTPMethod.GET,
    )
    path_template = models.CharField(max_length=255)
    query_parameters = models.JSONField(default=list, blank=True)
    body_parameter = models.CharField(max_length=120, blank=True, default="")
    input_schema = models.JSONField(default=dict, blank=True, null=True)
    output_schema = models.JSONField(default=dict, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("tool_id", "name", "id")
        constraints = [
            models.UniqueConstraint(
                fields=("tool", "slug"),
                name="nova_api_tool_operation_tool_slug_uniq",
            )
        ]

    def clean(self):
        super().clean()
        if self.tool_id and self.tool.tool_type != Tool.ToolType.API:
            raise ValidationError(_("API operations can only be attached to API tools."))

        slug = slugify(self.slug or self.name)
        if not slug:
            raise ValidationError(_("Operation slug cannot be empty."))
        self.slug = slug

        template = str(self.path_template or "").strip()
        if not template:
            raise ValidationError(_("The path template is required."))
        if not template.startswith("/"):
            raise ValidationError(_("The path template must start with '/'."))
        self.path_template = template

        query_parameters = self.query_parameters or []
        if not isinstance(query_parameters, list):
            raise ValidationError(_("Query parameters must be a list of field names."))

        normalized_query_parameters: list[str] = []
        seen: set[str] = set()
        for item in query_parameters:
            value = str(item or "").strip()
            if not value:
                continue
            if value in seen:
                continue
            seen.add(value)
            normalized_query_parameters.append(value)
        self.query_parameters = normalized_query_parameters

        self.body_parameter = str(self.body_parameter or "").strip()

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.tool.name}: {self.name}"
