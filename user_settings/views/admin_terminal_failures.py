from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Max, Q, Sum
from django.db.models.functions import Coalesce
from django.views.generic import ListView

from nova.models.TerminalCommandFailureMetric import TerminalCommandFailureMetric
from user_settings.mixins import StaffRequiredMixin


class AdminTerminalFailuresView(LoginRequiredMixin, StaffRequiredMixin, ListView):
    model = TerminalCommandFailureMetric
    template_name = "user_settings/admin_terminal_failures.html"
    context_object_name = "metrics"
    paginate_by = 50

    def get_template_names(self):
        if self.request.GET.get("partial") == "1":
            return ["user_settings/fragments/admin_terminal_failures.html"]
        return [self.template_name]

    def _query(self) -> str:
        return str(self.request.GET.get("q") or "").strip()

    def _runtime_engine(self) -> str:
        return str(self.request.GET.get("runtime_engine") or "").strip()

    def _failure_kind(self) -> str:
        return str(self.request.GET.get("failure_kind") or "").strip()

    def get_queryset(self):
        queryset = TerminalCommandFailureMetric.objects.all().order_by(
            "-last_seen_at",
            "-bucket_date",
            "head_command",
            "failure_kind",
        )
        query = self._query()
        runtime_engine = self._runtime_engine()
        failure_kind = self._failure_kind()

        if query:
            queryset = queryset.filter(
                Q(head_command__icontains=query) | Q(last_error__icontains=query)
            )
        if runtime_engine:
            queryset = queryset.filter(runtime_engine=runtime_engine)
        if failure_kind:
            queryset = queryset.filter(failure_kind=failure_kind)
        return queryset

    def _summary(self, queryset):
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

    def _querystring_without_page(self) -> str:
        query = self.request.GET.copy()
        query.pop("page", None)
        return query.urlencode()

    def _partial_querystring_without_page(self) -> str:
        query = self.request.GET.copy()
        query.pop("page", None)
        query["partial"] = "1"
        return query.urlencode()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        filtered_queryset = self.get_queryset()
        context["summary"] = self._summary(filtered_queryset)
        context["filters"] = {
            "q": self._query(),
            "runtime_engine": self._runtime_engine(),
            "failure_kind": self._failure_kind(),
        }
        context["runtime_engines"] = list(
            TerminalCommandFailureMetric.objects.order_by()
            .values_list("runtime_engine", flat=True)
            .distinct()
        )
        context["failure_kinds"] = list(
            TerminalCommandFailureMetric.objects.order_by()
            .values_list("failure_kind", flat=True)
            .distinct()
        )
        context["page_querystring"] = self._querystring_without_page()
        context["page_querystring_partial"] = self._partial_querystring_without_page()
        return context
