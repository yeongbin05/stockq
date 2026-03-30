import json
from types import SimpleNamespace
from unittest.mock import call, patch

from celery.exceptions import MaxRetriesExceededError
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
    dispatch_summary_jobs,
    fetch_favorite_news,
    generate_summary_for_stock,
)

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
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    def test_generate_summary_logs_no_news_when_no_news_exists(self):
        stock, job = self.create_job(symbol="AAPL", name="Apple")

        result = generate_summary_for_stock.apply(args=(job.id,)).get()

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

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
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

        result = generate_summary_for_stock.apply(args=(job.id,)).get()

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
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
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

        generate_summary_for_stock.apply(args=(job.id,)).get()
        generate_summary_for_stock.apply(args=(job.id,)).get()

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

class GenerateSummaryRetryTests(SummaryJobTestMixin,TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_retries_when_json_parse_fails(
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

        with self.assertRaises(MaxRetriesExceededError):
            generate_summary_for_stock.apply(args=(job.id,)).get()

        self.assertEqual(
            SummaryGenerationLog.objects.filter(
                stock=stock,
                status="failed",
                error_message__icontains="json_parse_failed",
            ).count(),
            4,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, SummaryJob.Status.FAILED)

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_retries_when_openai_call_fails(
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

        with self.assertRaises(MaxRetriesExceededError):
            generate_summary_for_stock.apply(args=(job.id,)).get()

        self.assertEqual(
            SummaryGenerationLog.objects.filter(
                stock=stock,
                status="failed",
            ).count(),
            4,
        )

        job.refresh_from_db()
        self.assertEqual(job.status, SummaryJob.Status.FAILED)

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

        self.assertEqual(result["dispatched_count"], 2)
        self.assertCountEqual(result["job_ids"], [pending_job_1.id, pending_job_2.id])

        self.assertEqual(pending_job_1.status, SummaryJob.Status.RUNNING)
        self.assertEqual(pending_job_2.status, SummaryJob.Status.RUNNING)
        self.assertIsNotNone(pending_job_1.started_at)
        self.assertIsNotNone(pending_job_2.started_at)

        self.assertEqual(already_running_job.status, SummaryJob.Status.RUNNING)

        mock_delay.assert_has_calls(
            [call(pending_job_1.id), call(pending_job_2.id)],
            any_order=True,
        )
        self.assertEqual(mock_delay.call_count, 2)

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
        mock_delay.assert_called_once_with(job.id)