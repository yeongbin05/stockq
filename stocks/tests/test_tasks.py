import json,uuid
from types import SimpleNamespace
from unittest.mock import ANY, call, patch
from uuid import uuid4
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from stocks.models import (
    FavoriteStock,
    News,
    Stock,
    Summary,
    SummaryGenerationLog,
    SummaryJob,
)
from stocks.tasks import (
    QUEUE_START_TIMEOUT,
    RUNNING_EXEC_TIMEOUT,
    MAX_STUCK_RECOVERY_RETRIES,
    dispatch_summary_jobs,
    fetch_favorite_news,
    generate_summary_for_stock,
    recover_stuck_summary_jobs,
)


def mark_job_as_dispatched(job):
    lease_token = str(uuid4())
    job.status = SummaryJob.Status.RUNNING
    job.lease_token = lease_token
    job.dispatched_at = timezone.now()
    job.save(update_fields=["status", "lease_token", "dispatched_at"])
    return lease_token


class SummaryJobTestMixin:
    def create_job(
        self,
        symbol="AAPL",
        name="Apple",
        *,
        status=SummaryJob.Status.PENDING,
        date=None,
    ):
        stock = Stock.objects.create(symbol=symbol, name=name)
        job = SummaryJob.objects.create(
            stock=stock,
            date=date or timezone.localdate(),
            status=status,
        )
        return stock, job


class GenerateSummaryEmptyBranchTests(SummaryJobTestMixin, TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test", OPENAI_BUCKET_ENABLED=False)
    def test_generate_summary_logs_no_news_when_no_news_exists(self):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        lease_token = mark_job_as_dispatched(job)
        result = generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        self.assertIsNotNone(result)
        self.assertEqual(Summary.objects.filter(stock=stock).count(), 0)

        job.refresh_from_db()
        self.assertEqual(job.status, SummaryJob.Status.NO_NEWS)
        self.assertIsNotNone(job.finished_at)

        log = SummaryGenerationLog.objects.filter(stock=stock).latest("created_at")
        self.assertEqual(log.status, "no_news")
        self.assertEqual(log.raw_count, 0)
        self.assertEqual(log.relevant_count, 0)
        self.assertEqual(log.before_input_tokens, 0)
        self.assertEqual(log.after_input_tokens, 0)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test", OPENAI_BUCKET_ENABLED=False)
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_logs_no_relevant_news_when_all_news_filtered_out(
        self,
        mock_score_news_relevance,
        mock_openai_create,
    ):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        news = News.objects.create(
            headline="Macro market roundup mentioning Apple once",
            url="https://example.com/aapl-macro",
            source="Example",
            published_at=timezone.now(),
            language="en",
            raw_json={},
        )
        news.stocks.add(stock)

        mock_score_news_relevance.return_value = (1, False, "not relevant enough")

        lease_token = mark_job_as_dispatched(job)
        result = generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        self.assertIsNotNone(result)
        self.assertEqual(Summary.objects.filter(stock=stock).count(), 0)
        self.assertEqual(mock_openai_create.call_count, 0)

        job.refresh_from_db()
        self.assertEqual(job.status, SummaryJob.Status.NO_RELEVANT_NEWS)
        self.assertIsNotNone(job.finished_at)

        log = SummaryGenerationLog.objects.filter(stock=stock).latest("created_at")
        self.assertEqual(log.status, "no_relevant_news")
        self.assertEqual(log.raw_count, 1)
        self.assertEqual(log.relevant_count, 0)


class GenerateSummaryIdempotencyTests(SummaryJobTestMixin, TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test", OPENAI_BUCKET_ENABLED=False)
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_does_not_create_duplicate_summary_for_same_stock_and_date(
        self,
        mock_score_news_relevance,
        mock_openai_create,
    ):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        news = News.objects.create(
            headline="Apple beats estimates",
            url="https://example.com/aapl-news",
            source="Example",
            published_at=timezone.now(),
            language="en",
            raw_json={},
        )
        news.stocks.add(stock)

        mock_score_news_relevance.return_value = (10, True, "matched")

        response_payload = {
            "ticker": "AAPL",
            "date": str(job.date),
            "news_summary": ["Apple beat earnings expectations."],
            "price_and_volume": "Stock moved on earnings sentiment.",
            "overall_sentiment": {
                "sentiment": "긍정",
                "rationale": "Strong earnings and positive guidance.",
                "confidence": 87,
            },
        }

        fake_response = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(response_payload))
                )
            ]
        )
        mock_openai_create.return_value = fake_response

        lease_token = mark_job_as_dispatched(job)
        generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        lease_token = mark_job_as_dispatched(job)
        generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        self.assertEqual(
            Summary.objects.filter(stock=stock, date=job.date).count(),
            1,
        )

        summary = Summary.objects.get(stock=stock, date=job.date)
        self.assertEqual(summary.stock, stock)
        self.assertEqual(summary.date, job.date)
        self.assertEqual(summary.summary["ticker"], "AAPL")
        self.assertEqual(summary.summary["overall_sentiment"]["sentiment"], "긍정")


class FetchFavoriteNewsJobCreationTests(TestCase):
    @patch("stocks.tasks.upsert_news_for_symbol")
    def test_fetch_favorite_news_continues_other_symbols_when_one_upsert_fails(
        self,
        mock_upsert_news,
    ):
        User = get_user_model()
        user = User.objects.create_user(
            email="testuser",
            password="test1234",
        )

        aapl = Stock.objects.create(symbol="AAPL", name="Apple")
        msft = Stock.objects.create(symbol="MSFT", name="Microsoft")
        nvda = Stock.objects.create(symbol="NVDA", name="NVIDIA")

        FavoriteStock.objects.create(user=user, stock=aapl)
        FavoriteStock.objects.create(user=user, stock=msft)
        FavoriteStock.objects.create(user=user, stock=nvda)

        def upsert_side_effect(symbol, days=1):
            if symbol == "AAPL":
                raise Exception("upsert failed for AAPL")
            return {
                "created_news": 1,
                "linked_pairs": 1,
                "skipped": 0,
            }

        mock_upsert_news.side_effect = upsert_side_effect

        result = fetch_favorite_news()

        self.assertEqual(mock_upsert_news.call_count, 3)

        self.assertEqual(
            SummaryJob.objects.filter(date=timezone.localdate()).count(),
            2,
        )
        self.assertCountEqual(
            list(
                SummaryJob.objects.filter(date=timezone.localdate())
                .values_list("stock__symbol", flat=True)
            ),
            ["MSFT", "NVDA"],
        )

        self.assertEqual(len(result["failed_symbols"]), 1)
        self.assertEqual(result["failed_symbols"][0]["symbol"], "AAPL")
        self.assertCountEqual(result["success_symbols"], ["MSFT", "NVDA"])
        self.assertCountEqual(result["enqueued_symbols"], ["MSFT", "NVDA"])


class GenerateSummaryFailureTests(SummaryJobTestMixin, TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test", OPENAI_BUCKET_ENABLED=False)
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_fails_and_logs_when_json_parse_fails(
        self,
        mock_score_news_relevance,
        mock_openai_create,
    ):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        news = News.objects.create(
            headline="Apple beats estimates",
            url="https://example.com/aapl-news",
            source="Example",
            published_at=timezone.now(),
            language="en",
            raw_json={},
        )
        news.stocks.add(stock)

        mock_score_news_relevance.return_value = (10, True, "matched")
        mock_openai_create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))]
        )

        lease_token = mark_job_as_dispatched(job)

        with self.assertRaises(Exception) as cm:
            generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        self.assertIn("Expecting value", str(cm.exception))
        self.assertEqual(
            Summary.objects.filter(stock=stock, date=job.date).count(),
            0,
        )

        log = SummaryGenerationLog.objects.get(stock=stock, status="failed")
        self.assertIn("json_parse_failed", log.error_message)
        self.assertEqual(log.raw_count, 1)
        self.assertEqual(log.relevant_count, 1)

        job.refresh_from_db()
        self.assertEqual(job.status, SummaryJob.Status.FAILED)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("json_parse_failed", job.error_message)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test", OPENAI_BUCKET_ENABLED=False)
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_fails_and_logs_when_openai_call_fails(
        self,
        mock_score_news_relevance,
        mock_openai_create,
    ):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        news = News.objects.create(
            headline="Apple beats estimates",
            url="https://example.com/aapl-news",
            source="Example",
            published_at=timezone.now(),
            language="en",
            raw_json={},
        )
        news.stocks.add(stock)

        mock_score_news_relevance.return_value = (10, True, "matched")
        mock_openai_create.side_effect = Exception("openai temporary failure")

        lease_token = mark_job_as_dispatched(job)

        with self.assertRaises(Exception) as cm:
            generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        self.assertIn("openai temporary failure", str(cm.exception))
        self.assertEqual(
            Summary.objects.filter(stock=stock, date=job.date).count(),
            0,
        )

        log = SummaryGenerationLog.objects.get(stock=stock, status="failed")
        self.assertIn("openai temporary failure", log.error_message)
        self.assertEqual(log.raw_count, 1)
        self.assertEqual(log.relevant_count, 1)

        job.refresh_from_db()
        self.assertEqual(job.status, SummaryJob.Status.FAILED)
        self.assertIsNotNone(job.started_at)
        self.assertIsNotNone(job.finished_at)
        self.assertIn("openai temporary failure", job.error_message)


class FetchFavoriteNewsDuplicateJobTests(TestCase):
    @patch("stocks.tasks.upsert_news_for_symbol")
    def test_fetch_favorite_news_does_not_create_duplicate_job_for_same_stock_and_date(
        self,
        mock_upsert_news,
    ):
        User = get_user_model()
        user = User.objects.create_user(
            email="testuser2",
            password="test1234",
        )

        aapl = Stock.objects.create(symbol="AAPL", name="Apple")
        FavoriteStock.objects.create(user=user, stock=aapl)

        mock_upsert_news.return_value = {
            "created_news": 1,
            "linked_pairs": 1,
            "skipped": 0,
        }

        fetch_favorite_news()
        fetch_favorite_news()

        self.assertEqual(
            SummaryJob.objects.filter(
                stock=aapl,
                date=timezone.localdate(),
            ).count(),
            1,
        )


class DispatchSummaryJobsTests(TestCase):
    @patch("stocks.tasks.generate_summary_for_stock.delay")
    def test_dispatch_summary_jobs_moves_only_pending_jobs_to_running_and_enqueues_them(
        self,
        mock_delay,
    ):
        today = timezone.localdate()

        aapl = Stock.objects.create(symbol="AAPL", name="Apple")
        msft = Stock.objects.create(symbol="MSFT", name="Microsoft")
        nvda = Stock.objects.create(symbol="NVDA", name="NVIDIA")

        pending_job_1 = SummaryJob.objects.create(
            stock=aapl,
            date=today,
            status=SummaryJob.Status.PENDING,
        )
        pending_job_2 = SummaryJob.objects.create(
            stock=msft,
            date=today,
            status=SummaryJob.Status.PENDING,
        )
        already_running_job = SummaryJob.objects.create(
            stock=nvda,
            date=today,
            status=SummaryJob.Status.RUNNING,
            started_at=timezone.now(),
        )

        result = dispatch_summary_jobs(limit=10)

        pending_job_1.refresh_from_db()
        pending_job_2.refresh_from_db()
        already_running_job.refresh_from_db()

        self.assertEqual(result["dispatched_count"], 1)
        self.assertEqual(len(result["job_ids"]), 1)

        running_jobs = SummaryJob.objects.filter(status=SummaryJob.Status.RUNNING)
        self.assertEqual(running_jobs.count(), 2)

        self.assertEqual(already_running_job.status, SummaryJob.Status.RUNNING)

        dispatched_pending_jobs = SummaryJob.objects.filter(
            id__in=[pending_job_1.id, pending_job_2.id],
            status=SummaryJob.Status.RUNNING,
        )
        self.assertEqual(dispatched_pending_jobs.count(), 1)

        still_pending_jobs = SummaryJob.objects.filter(
            id__in=[pending_job_1.id, pending_job_2.id],
            status=SummaryJob.Status.PENDING,
        )
        self.assertEqual(still_pending_jobs.count(), 1)

        dispatched_job = dispatched_pending_jobs.first()
        self.assertIsNotNone(dispatched_job.dispatched_at)
        self.assertIsNotNone(dispatched_job.lease_token)
        self.assertIsNone(dispatched_job.started_at)

        mock_delay.assert_called_once_with(dispatched_job.id, ANY)

    @patch("stocks.tasks.generate_summary_for_stock.delay")
    def test_dispatch_summary_jobs_does_not_redispatch_jobs_already_marked_running(
        self,
        mock_delay,
    ):
        stock = Stock.objects.create(symbol="AAPL", name="Apple")
        job = SummaryJob.objects.create(
            stock=stock,
            date=timezone.localdate(),
            status=SummaryJob.Status.PENDING,
        )

        first_result = dispatch_summary_jobs(limit=10)
        second_result = dispatch_summary_jobs(limit=10)

        job.refresh_from_db()

        self.assertEqual(first_result["dispatched_count"], 1)
        self.assertEqual(second_result["dispatched_count"], 0)

        self.assertEqual(job.status, SummaryJob.Status.RUNNING)
        mock_delay.assert_called_once_with(job.id, ANY)


class GenerateSummaryRateLimitTests(TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.get_openai_bucket")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_moves_job_to_retry_wait_when_bucket_denies(
        self,
        mock_score_news_relevance,
        mock_get_openai_bucket,
        mock_openai_create,
    ):
        stock = Stock.objects.create(symbol="AAPL", name="Apple")
        news = News.objects.create(
            headline="Apple beats estimates",
            url="https://example.com/aapl-news",
            source="Example",
            published_at=timezone.now(),
            language="en",
            raw_json={},
        )
        news.stocks.add(stock)

        lease_token = uuid.uuid4()
        job = SummaryJob.objects.create(
            stock=stock,
            date=timezone.localdate(),
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
            dispatched_at=timezone.now(),
            started_at=None,
        )

        mock_score_news_relevance.return_value = (90, True, "relevant")
        mock_bucket = mock_get_openai_bucket.return_value
        mock_bucket.consume.return_value = SimpleNamespace(
            allowed=False,
            remaining_tokens=0,
            retry_after_seconds=3,
        )

        result = generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        job.refresh_from_db()

        self.assertEqual(result["status"], "rate_limited")
        self.assertEqual(result["retry_after"], 3)

        self.assertEqual(job.status, SummaryJob.Status.RETRY_WAIT)
        self.assertIsNotNone(job.retry_at)
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.dispatched_at)
        self.assertIsNone(job.lease_token)
        self.assertEqual(job.error_message, "rate limited by openai bucket")

        mock_openai_create.assert_not_called()
        self.assertFalse(
            SummaryGenerationLog.objects.filter(
                stock=stock,
                date=job.date,
                status="failed",
            ).exists()
        )


class DispatchSummaryRetryWaitTests(TestCase):
    @patch("stocks.tasks.generate_summary_for_stock.delay")
    def test_dispatch_summary_jobs_requeues_retry_wait_job(self, mock_delay):
        stock = Stock.objects.create(symbol="AAPL", name="Apple")
        job = SummaryJob.objects.create(
            stock=stock,
            date=timezone.localdate(),
            status=SummaryJob.Status.RETRY_WAIT,
            retry_at=timezone.now() - timedelta(seconds=1),
            lease_token=None,
            started_at=None,
            dispatched_at=None,
        )

        result = dispatch_summary_jobs()

        job.refresh_from_db()

        self.assertEqual(result["dispatched_count"], 1)
        self.assertEqual(result["job_ids"], [job.id])

        self.assertEqual(job.status, SummaryJob.Status.RUNNING)
        self.assertIsNotNone(job.dispatched_at)
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.retry_at)
        self.assertIsNotNone(job.lease_token)
        self.assertEqual(job.error_message, "")

        mock_delay.assert_called_once_with(job.id, str(job.lease_token))



class DispatchSummaryRetryWaitNotReadyTests(TestCase):
    @patch("stocks.tasks.generate_summary_for_stock.delay")
    def test_dispatch_summary_jobs_does_not_requeue_retry_wait_job_before_retry_at(
        self,
        mock_delay,
    ):
        stock = Stock.objects.create(symbol="AAPL", name="Apple")
        job = SummaryJob.objects.create(
            stock=stock,
            date=timezone.localdate(),
            status=SummaryJob.Status.RETRY_WAIT,
            retry_at=timezone.now() + timedelta(minutes=1),
            lease_token=None,
            started_at=None,
            dispatched_at=None,
        )

        result = dispatch_summary_jobs()

        job.refresh_from_db()

        self.assertEqual(result["dispatched_count"], 0)
        self.assertEqual(result["job_ids"], [])

        self.assertEqual(job.status, SummaryJob.Status.RETRY_WAIT)
        self.assertIsNone(job.dispatched_at)
        self.assertIsNone(job.started_at)
        self.assertIsNotNone(job.retry_at)
        mock_delay.assert_not_called()


class RecoverStuckSummaryJobsTests(TestCase):
    def test_recover_stuck_summary_jobs_moves_stuck_running_job_to_retry_wait(self):
        stock = Stock.objects.create(symbol="AAPL", name="Apple")
        lease_token = str(uuid4())

        job = SummaryJob.objects.create(
            stock=stock,
            date=timezone.localdate(),
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
            retry_count=0,
            dispatched_at=timezone.now() - QUEUE_START_TIMEOUT - timedelta(seconds=1),
            started_at=None,
            finished_at=None,
        )

        result = recover_stuck_summary_jobs()

        job.refresh_from_db()

        self.assertEqual(result["recovered_job_ids"], [job.id])
        self.assertEqual(result["failed_job_ids"], [])

        self.assertEqual(job.status, SummaryJob.Status.RETRY_WAIT)
        self.assertEqual(job.retry_count, 1)
        self.assertIsNotNone(job.retry_at)
        self.assertIsNone(job.dispatched_at)
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.lease_token)
        self.assertEqual(job.error_message, "stuck timeout recovery")

    def test_recover_stuck_summary_jobs_marks_job_failed_when_max_retries_exceeded(self):
        stock = Stock.objects.create(symbol="AAPL", name="Apple")
        lease_token = str(uuid4())

        job = SummaryJob.objects.create(
            stock=stock,
            date=timezone.localdate(),
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
            retry_count=MAX_STUCK_RECOVERY_RETRIES,
            started_at=timezone.now() - RUNNING_EXEC_TIMEOUT - timedelta(seconds=1),
            finished_at=None,
        )

        result = recover_stuck_summary_jobs()

        job.refresh_from_db()

        self.assertEqual(result["recovered_job_ids"], [])
        self.assertEqual(result["failed_job_ids"], [job.id])

        self.assertEqual(job.status, SummaryJob.Status.FAILED)
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.error_message, "stuck timeout exceeded max retries")


class GenerateSummaryLeaseStateTests(SummaryJobTestMixin, TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test", OPENAI_BUCKET_ENABLED=False)
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    @patch("stocks.tasks._is_current_lease")
    def test_generate_summary_returns_stale_before_llm(
        self,
        mock_is_current_lease,
        mock_score_news_relevance,
        mock_openai_create,
    ):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        news = News.objects.create(
            headline="Apple beats estimates",
            url="https://example.com/aapl-news",
            source="Example",
            published_at=timezone.now(),
            language="en",
            raw_json={},
        )
        news.stocks.add(stock)

        mock_score_news_relevance.return_value = (10, True, "matched")
        mock_is_current_lease.return_value = False

        lease_token = mark_job_as_dispatched(job)
        result = generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        job.refresh_from_db()

        self.assertEqual(result["status"], "stale_before_llm")
        self.assertEqual(Summary.objects.filter(stock=stock, date=job.date).count(), 0)
        mock_openai_create.assert_not_called()
        self.assertEqual(job.status, SummaryJob.Status.RUNNING)
        self.assertIsNotNone(job.started_at)
        self.assertIsNone(job.finished_at)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    @patch("stocks.tasks.get_openai_bucket")
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    @patch("stocks.tasks._is_current_lease")
    def test_generate_summary_returns_stale_after_llm(
        self,
        mock_is_current_lease,
        mock_score_news_relevance,
        mock_openai_create,
        mock_get_openai_bucket,
    ):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        news = News.objects.create(
            headline="Apple beats estimates",
            url="https://example.com/aapl-news",
            source="Example",
            published_at=timezone.now(),
            language="en",
            raw_json={},
        )
        news.stocks.add(stock)

        mock_score_news_relevance.return_value = (10, True, "matched")
        mock_is_current_lease.side_effect = [True, False]

        mock_bucket = mock_get_openai_bucket.return_value
        mock_bucket.consume.return_value = SimpleNamespace(
            allowed=True,
            remaining_tokens=0,
            retry_after_seconds=0,
        )

        response_payload = {
            "ticker": "AAPL",
            "date": str(job.date),
            "news_summary": ["Apple beat earnings expectations."],
            "price_and_volume": "Stock moved on earnings sentiment.",
            "overall_sentiment": {
                "sentiment": "긍정",
                "rationale": "Strong earnings and positive guidance.",
                "confidence": 87,
            },
        }
        mock_openai_create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=json.dumps(response_payload))
                )
            ]
        )

        lease_token = mark_job_as_dispatched(job)
        result = generate_summary_for_stock.apply(args=(job.id, lease_token)).get()

        job.refresh_from_db()

        self.assertEqual(result["status"], "stale_after_llm")
        self.assertEqual(Summary.objects.filter(stock=stock, date=job.date).count(), 0)
        self.assertEqual(job.status, SummaryJob.Status.RUNNING)
        self.assertIsNotNone(job.started_at)
        self.assertIsNone(job.finished_at)




