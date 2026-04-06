from __future__ import annotations

from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views import View

from nova.models.TerminalCommandFailureMetric import TerminalCommandFailureMetric
from user_settings.mixins import StaffRequiredMixin


FILTER_KEYS = ("q", "runtime_engine", "failure_kind")
PAGE_SIZE = 50
PURGE_AGE_CHOICES = (
    ("", _("All matched")),
    ("7", _("Older than 7 days")),
    ("30", _("Older than 30 days")),
    ("90", _("Older than 90 days")),
    ("180", _("Older than 180 days")),
    ("365", _("Older than 365 days")),
)
ALLOWED_PURGE_AGE_VALUES = {value for value, _label in PURGE_AGE_CHOICES if value}


def _extract_metric_filters(source) -> dict[str, str]:
    return {
        key: str(source.get(key) or "").strip()
        for key in FILTER_KEYS
    }


def _serialize_metric_filters(filters: dict[str, str]) -> str:
    query = {
        key: value
        for key, value in filters.items()
        if str(value or "").strip()
    }
    if not query:
        return ""
    from urllib.parse import urlencode

    return urlencode(query)


def _admin_metrics_url(filters: dict[str, str] | None = None) -> str:
    base_url = reverse("user_settings:admin-metrics")
    if not filters:
        return base_url
    querystring = _serialize_metric_filters(filters)
    return f"{base_url}?{querystring}" if querystring else base_url


def _filtered_metrics_queryset(filters: dict[str, str]):
    queryset = TerminalCommandFailureMetric.objects.all().order_by(
        "-last_seen_at",
        "-bucket_date",
        "head_command",
        "failure_kind",
    )
    query = str(filters.get("q") or "").strip()
    runtime_engine = str(filters.get("runtime_engine") or "").strip()
    failure_kind = str(filters.get("failure_kind") or "").strip()

    if query:
        queryset = queryset.filter(
            Q(head_command__icontains=query) | Q(last_error__icontains=query)
        )
    if runtime_engine:
        queryset = queryset.filter(runtime_engine=runtime_engine)
    if failure_kind:
        queryset = queryset.filter(failure_kind=failure_kind)
    return queryset


def _metrics_summary(queryset) -> dict:
    aggregate = queryset.aggregate(
        total_events=Coalesce(Sum("count"), 0),
        total_groups=Count("id"),
        latest_seen=Max("last_seen_at"),
    )
    top_command = (
        queryset.exclude(head_command="")
        .values("head_command")
        .annotate(total=Coalesce(Sum("count"), 0))
        .order_by("-total", "head_command")
        .first()
    )
    top_kind = (
        queryset.values("failure_kind")
        .annotate(total=Coalesce(Sum("count"), 0))
        .order_by("-total", "failure_kind")
        .first()
    )
    return {
        "total_events": int(aggregate["total_events"] or 0),
        "total_groups": int(aggregate["total_groups"] or 0),
        "latest_seen": aggregate["latest_seen"],
        "top_command": top_command,
        "top_kind": top_kind,
    }


def _page_querystring(filters: dict[str, str]) -> str:
    return _serialize_metric_filters(filters)


def _parse_purge_age(raw_value: str | None) -> int | None:
    value = str(raw_value or "").strip()
    if not value:
        return None
    if value not in ALLOWED_PURGE_AGE_VALUES:
        raise ValueError("invalid")
    return int(value)


class AdminMetricsView(LoginRequiredMixin, StaffRequiredMixin, View):
    template_name = "user_settings/admin_metrics.html"

    def get_filters(self) -> dict[str, str]:
        return _extract_metric_filters(self.request.GET)

    def get(self, request, *args, **kwargs):
        filters = self.get_filters()
        filtered_queryset = _filtered_metrics_queryset(filters)
        paginator = Paginator(filtered_queryset, PAGE_SIZE)
        page_obj = paginator.get_page(request.GET.get("page"))
        metrics_rows = list(page_obj.object_list)

        context = {
            "summary": _metrics_summary(filtered_queryset),
            "filters": filters,
            "metrics": metrics_rows,
            "metrics_rows": metrics_rows,
            "displayed_rows_count": len(metrics_rows),
            "page_obj": page_obj,
            "paginator": paginator,
            "is_paginated": paginator.num_pages > 1,
            "runtime_engines": list(
                TerminalCommandFailureMetric.objects.order_by()
                .values_list("runtime_engine", flat=True)
                .distinct()
            ),
            "failure_kinds": list(
                TerminalCommandFailureMetric.objects.order_by()
                .values_list("failure_kind", flat=True)
                .distinct()
            ),
            "page_querystring": _page_querystring(filters),
            "purge_age_choices": PURGE_AGE_CHOICES,
        }
        return render(request, self.template_name, context)


class AdminMetricsDeleteView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request, pk: int):
        filters = _extract_metric_filters(request.POST)
        redirect_url = _admin_metrics_url(filters)
        if request.POST.get("confirm") != "1":
            messages.error(request, _("Deletion confirmation was missing."))
            return redirect(redirect_url)

        metric = get_object_or_404(TerminalCommandFailureMetric, pk=pk)
        label = (
            f"{metric.bucket_date} / {metric.runtime_engine} / "
            f"{metric.head_command or '(empty)'} / {metric.failure_kind}"
        )
        metric.delete()
        messages.success(request, _("Deleted metrics bucket: %(label)s.") % {"label": label})
        return redirect(redirect_url)


class AdminMetricsPurgeView(LoginRequiredMixin, StaffRequiredMixin, View):
    def post(self, request):
        filters = _extract_metric_filters(request.POST)
        redirect_url = _admin_metrics_url(filters)
        if request.POST.get("confirm") != "1":
            messages.error(request, _("Purge confirmation was missing."))
            return redirect(redirect_url)

        try:
            older_than_days = _parse_purge_age(request.POST.get("older_than_days"))
        except ValueError:
            messages.error(request, _("Invalid purge age selection."))
            return redirect(redirect_url)

        queryset = _filtered_metrics_queryset(filters)
        if older_than_days is not None:
            cutoff = timezone.now() - timedelta(days=older_than_days)
            queryset = queryset.filter(last_seen_at__lt=cutoff)

        matched_count = queryset.count()
        if matched_count == 0:
            messages.info(request, _("No metrics buckets matched the current cleanup selection."))
            return redirect(redirect_url)

        queryset.delete()
        if older_than_days is None:
            messages.success(request, _("Purged %(count)s metrics bucket(s).") % {"count": matched_count})
        else:
            messages.success(
                request,
                _("Purged %(count)s metrics bucket(s) older than %(days)s days.")
                % {"count": matched_count, "days": older_than_days},
            )
        return redirect(redirect_url)
