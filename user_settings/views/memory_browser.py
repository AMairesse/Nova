from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from nova.models.Memory import MemoryItem, MemoryItemStatus


class MemoryItemsListView(LoginRequiredMixin, ListView):
    """Read-only browser for long-term memory items."""

    model = MemoryItem
    template_name = "user_settings/fragments/memory_items_table.html"
    context_object_name = "items"
    paginate_by = 50

    def _include_archived(self) -> bool:
        v = (self.request.GET.get("include_archived") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["include_archived"] = self._include_archived()
        return context

    def get_queryset(self):
        qs = (
            MemoryItem.objects.filter(user=self.request.user)
            .select_related("theme")
            .select_related("embedding")
            .order_by("-created_at")
        )

        # Default: show active items only. If `include_archived=1`, show all.
        if not self._include_archived():
            qs = qs.filter(status=MemoryItemStatus.ACTIVE)

        theme = (self.request.GET.get("theme") or "").strip().lower()
        if theme:
            qs = qs.filter(theme__slug=theme)

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(content__icontains=q)

        return qs
