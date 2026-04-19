from django.apps import AppConfig
from prometheus_client import REGISTRY


class StocksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "stocks"

    def ready(self):
        from .metrics import (
            SummaryJobFinishedTotalCollector,
            SummaryJobQueueWaitSecondsCollector,
            SummaryJobStatusCollector,
            SummaryJobStuckTotalCollector,
            SummaryJobTotalElapsedSecondsCollector,
            SummaryJobsOldestPendingSecondsCollector,
        )

        collectors = [
            SummaryJobStatusCollector(),
            SummaryJobsOldestPendingSecondsCollector(),
            SummaryJobFinishedTotalCollector(),
            SummaryJobQueueWaitSecondsCollector(),
            SummaryJobTotalElapsedSecondsCollector(),
            SummaryJobStuckTotalCollector(),
        ]

        for collector in collectors:
            try:
                REGISTRY.register(collector)
            except ValueError:
                pass