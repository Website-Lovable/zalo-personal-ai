from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "apps.dashboard"
    label = "dashboard"
    verbose_name = "Dashboard"

    def ready(self):
        from . import receivers

        receivers.connect()
