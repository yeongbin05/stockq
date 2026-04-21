import math
from .models import SummaryJob
from datetime import timedelta
from django.conf import settings
from django.db.models import Count,Q
from django.utils import timezone
from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily,CounterMetricFamily
from prometheus_client.registry import Collector

TERMINAL_STATUSES = [
    SummaryJob.Status.SUCCESS,
    SummaryJob.Status.FAILED,
    SummaryJob.Status.NO_NEWS,
    SummaryJob.Status.NO_RELEVANT_NEWS,
]

API_REQUESTS_TOTAL = Counter(
    "stockq_api_requests_total",
    "Total API requests",
    ["route", "method", "status"],
)

API_REQUEST_LATENCY_SECONDS = Histogram(
    "stockq_api_request_latency_seconds",
    "API request latency in seconds",
    ["route", "method"],
    buckets=(0.01, 0.03, 0.05, 0.1, 0.2, 0.3, 0.5, 1, 2, 5),
)

def _percentile(values, p: float) -> float:
    if not values:
        return 0.0

    values = sorted(values)
    if len(values) == 1:
        return float(values[0])

    rank = (p / 100.0) * (len(values) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)

    if low == high:
        return float(values[int(low)])

    low_value = values[int(low)]
    high_value = values[int(high)]
    weight = rank - low
    return float(low_value * (1 - weight) + high_value * weight)


def _duration_stats(values) -> dict[str, float]:
    if not values:
        return {
            "avg": 0.0,
            "p95": 0.0,
            "max": 0.0,
        }

    return {
        "avg": float(sum(values) / len(values)),
        "p95": _percentile(values, 95),
        "max": float(max(values)),
    }


class SummaryJobFinishedTotalCollector(Collector):
    def collect(self):
        metric = CounterMetricFamily(
            "summary_job_finished_total",
            "Total number of finished SummaryJob rows by terminal status",
            labels=["status"],
        )

        rows = (
            SummaryJob.objects
            .filter(status__in=TERMINAL_STATUSES)
            .values("status")
            .annotate(count=Count("id"))
        )

        for row in rows:
            metric.add_metric([row["status"]], row["count"])

        yield metric

    def describe(self):
        return []


class SummaryJobQueueWaitSecondsCollector(Collector):
    def collect(self):
        metric = GaugeMetricFamily(
            "summary_job_queue_wait_seconds",
            "Queue wait stats in seconds for finished SummaryJobs",
            labels=["stat"],
        )

        rows = (
            SummaryJob.objects
            .filter(
                status__in=TERMINAL_STATUSES,
                dispatched_at__isnull=False,
                started_at__isnull=False,
            )
            .values_list("dispatched_at", "started_at")
        )

        values = []
        for dispatched_at, started_at in rows:
            if started_at >= dispatched_at:
                values.append((started_at - dispatched_at).total_seconds())

        stats = _duration_stats(values)

        for stat_name, value in stats.items():
            metric.add_metric([stat_name], value)

        yield metric

    def describe(self):
        return []


class SummaryJobTotalElapsedSecondsCollector(Collector):
    def collect(self):
        metric = GaugeMetricFamily(
            "summary_job_total_elapsed_seconds",
            "End-to-end elapsed stats in seconds for finished SummaryJobs",
            labels=["stat"],
        )

        rows = (
            SummaryJob.objects
            .filter(
                status__in=TERMINAL_STATUSES,
                created_at__isnull=False,
                finished_at__isnull=False,
            )
            .values_list("created_at", "finished_at")
        )

        values = []
        for created_at, finished_at in rows:
            if finished_at >= created_at:
                values.append((finished_at - created_at).total_seconds())

        stats = _duration_stats(values)

        for stat_name, value in stats.items():
            metric.add_metric([stat_name], value)

        yield metric

    def describe(self):
        return []


class SummaryJobStuckTotalCollector(Collector):
    def collect(self):
        metric = GaugeMetricFamily(
            "summary_job_stuck_total",
            "Current number of stuck SummaryJob rows",
        )

        stuck_seconds = getattr(settings, "SUMMARY_JOB_STUCK_SECONDS", 600)
        cutoff = timezone.now() - timedelta(seconds=stuck_seconds)

        stuck_count = (
            SummaryJob.objects
            .filter(status=SummaryJob.Status.RUNNING)
            .filter(
                Q(started_at__lt=cutoff) |
                Q(started_at__isnull=True, dispatched_at__lt=cutoff)
            )
            .count()
        )

        metric.add_metric([], stuck_count)
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