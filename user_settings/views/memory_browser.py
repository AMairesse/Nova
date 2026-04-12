from asgiref.sync import async_to_sync
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import ListView

from nova.memory.service import list_memory_documents_overview
from nova.models.MemoryDocument import MemoryDocument


class MemoryItemsListView(LoginRequiredMixin, ListView):
    """Read-only browser for long-term memory documents."""

    model = MemoryDocument
    template_name = "user_settings/fragments/memory_items_table.html"
    context_object_name = "documents"
    paginate_by = 50

    def _include_archived(self) -> bool:
        v = (self.request.GET.get("include_archived") or "").strip().lower()
        return v in {"1", "true", "yes", "on"}

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["include_archived"] = self._include_archived()
        context["items"] = context.get("documents", [])
        return context

    def get_queryset(self):
        include_archived = self._include_archived()
        q = (self.request.GET.get("q") or "").strip()
        return async_to_sync(list_memory_documents_overview)(
            user=self.request.user,
            include_archived=include_archived,
            q=q,
        )
