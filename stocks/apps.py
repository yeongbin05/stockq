from django.apps import AppConfig
from prometheus_client import REGISTRY


class StocksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stocks"

    def ready(self):
        from .metrics import SummaryJobStatusCollector

        try:
            REGISTRY.register(SummaryJobStatusCollector())
        except ValueError:
            pass