from django.apps import AppConfig


class AivaIntegrationConfig(AppConfig):
    name = "apps.integrations.aiva"
    label = "aiva"
    verbose_name = "Tích hợp AIVA"

    def ready(self):
        from . import receivers

        receivers.connect()
