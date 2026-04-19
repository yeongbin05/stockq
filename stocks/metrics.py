from django.db.models import Count
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector

from .models import SummaryJob


class SummaryJobStatusCollector(Collector):
    def collect(self):
        metric = GaugeMetricFamily(
            "summary_jobs_by_status",
            "Current number of SummaryJob rows by status",
            labels=["status"],
        )

        rows = (
            SummaryJob.objects
            .values("status")
            .annotate(count=Count("id"))
        )

        for row in rows:
            metric.add_metric([row["status"]], row["count"])

        yield metric

    def describe(self):
        return []