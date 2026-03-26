from types import SimpleNamespace
from celery.exceptions import MaxRetriesExceededError
from django.test import TestCase, override_settings
from django.utils import timezone

from stocks.models import News, Stock, SummaryGenerationLog
from stocks.tasks import generate_summary_for_stock

from unittest.mock import call, patch

from stocks.models import Stock, FavoriteStock
from stocks.tasks import fetch_favorite_news

from django.contrib.auth import get_user_model

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from stocks.models import News, Stock, Summary
from stocks.tasks import generate_summary_for_stock

from django.test import TestCase, override_settings
from django.utils import timezone
from unittest.mock import patch

from stocks.models import News, Stock, Summary, SummaryGenerationLog
from stocks.tasks import generate_summary_for_stock


class GenerateSummaryEmptyBranchTests(TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    def test_generate_summary_logs_no_news_when_no_news_exists(self):
        stock = Stock.objects.create(symbol="AAPL", name="Apple")

        result = generate_summary_for_stock.apply(args=("AAPL",)).get()

        self.assertIsNotNone(result)
        self.assertEqual(Summary.objects.filter(stock=stock).count(), 0)

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
        stock = Stock.objects.create(symbol="AAPL", name="Apple")

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

        result = generate_summary_for_stock.apply(args=("AAPL",)).get()

        self.assertIsNotNone(result)
        self.assertEqual(Summary.objects.filter(stock=stock).count(), 0)
        self.assertEqual(mock_openai_create.call_count, 0)

        log = SummaryGenerationLog.objects.filter(stock=stock).latest("created_at")
        self.assertEqual(log.status, "no_relevant_news")
        self.assertEqual(log.raw_count, 1)
        self.assertEqual(log.relevant_count, 0)
        

class GenerateSummaryIdempotencyTests(TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_does_not_create_duplicate_summary_for_same_stock_and_date(
        self,
        mock_score_news_relevance,
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

        mock_score_news_relevance.return_value = (10, True, "matched")

        response_payload = {
            "ticker": "AAPL",
            "date": str(timezone.localdate()),
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

        generate_summary_for_stock.apply(args=("AAPL",)).get()
        generate_summary_for_stock.apply(args=("AAPL",)).get()

        today = timezone.localdate()

        self.assertEqual(
            Summary.objects.filter(stock=stock, date=today).count(),
            1,
        )

        summary = Summary.objects.get(stock=stock, date=today)
        self.assertEqual(summary.stock, stock)
        self.assertEqual(summary.date, today)
        self.assertEqual(summary.summary["ticker"], "AAPL")
        self.assertEqual(summary.summary["overall_sentiment"]["sentiment"], "긍정")

class FetchFavoriteNewsIsolationTests(TestCase):
    @patch("stocks.tasks.generate_summary_for_stock.delay")
    @patch("stocks.tasks.upsert_news_for_symbol")
    def test_fetch_favorite_news_continues_other_symbols_when_one_summary_enqueue_fails(
        self,
        mock_upsert_news,
        mock_generate_summary_delay,
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

        mock_upsert_news.return_value = {
            "created_news": 1,
            "linked_pairs": 1,
            "skipped": 0,
        }

        def delay_side_effect(symbol):
            if symbol == "AAPL":
                raise Exception("summary enqueue failed for AAPL")
            return None

        mock_generate_summary_delay.side_effect = delay_side_effect

        result = fetch_favorite_news()

        self.assertEqual(mock_upsert_news.call_count, 3)
        self.assertEqual(mock_generate_summary_delay.call_count, 3)

        mock_upsert_news.assert_has_calls(
            [
                call("AAPL", days=1),
                call("MSFT", days=1),
                call("NVDA", days=1),
            ],
            any_order=True,
        )

        mock_generate_summary_delay.assert_has_calls(
            [
                call("AAPL"),
                call("MSFT"),
                call("NVDA"),
            ],
            any_order=True,
        )

        self.assertIn("AAPL", result["failed_symbols"])
        self.assertIn("MSFT", result["success_symbols"])
        self.assertIn("NVDA", result["success_symbols"])

class GenerateSummaryRetryTests(TestCase):
    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_retries_when_json_parse_fails(
        self,
        mock_score_news_relevance,
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

        mock_score_news_relevance.return_value = (10, True, "matched")

        fake_response = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="not-json"))]
        )
        mock_openai_create.return_value = fake_response

        with self.assertRaises(MaxRetriesExceededError):
            generate_summary_for_stock.apply(args=("AAPL",)).get()

        self.assertEqual(
            SummaryGenerationLog.objects.filter(
                stock=stock,
                status="failed",
                error_message__icontains="json_parse_failed",
            ).count(),
            4,
        )

    @override_settings(OPENAI_API_KEY="test-key", OPENAI_MODEL="gpt-test")
    @patch("stocks.tasks.openai.chat.completions.create")
    @patch("stocks.tasks.score_news_relevance")
    def test_generate_summary_retries_when_openai_call_fails(
        self,
        mock_score_news_relevance,
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

        mock_score_news_relevance.return_value = (10, True, "matched")
        mock_openai_create.side_effect = Exception("openai temporary failure")

        with self.assertRaises(MaxRetriesExceededError):
            generate_summary_for_stock.apply(args=("AAPL",)).get()

        self.assertEqual(
            SummaryGenerationLog.objects.filter(
                stock=stock,
                status="failed",
            ).count(),
            4,
        )