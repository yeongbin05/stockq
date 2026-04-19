from .models import SummaryJob
from django.db.models import Count
from django.utils import timezone
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector





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


class SummaryJobsOldestPendingSecondsCollector(Collector):
    def collect(self):
        metric = GaugeMetricFamily(
            "summary_jobs_oldest_pending_seconds",
            "Age in seconds of the oldest pending SummaryJob",
        )

        oldest_pending = (
            SummaryJob.objects
            .filter(status=SummaryJob.Status.PENDING)
            .order_by("created_at")
            .first()
        )

        value = 0
        if oldest_pending is not None:
            value = (timezone.now() - oldest_pending.created_at).total_seconds()

        metric.add_metric([], value)
        yield metric

    def describe(self):
        return []