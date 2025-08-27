# user_settings/views/dashboard.py
from django.views.generic import TemplateView


class DashboardView(TemplateView):
    template_name = "user_settings/dashboard.html"
