from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from stocks.rate_limit import get_finnhub_bucket
from stocks.services import upsert_news_for_symbol
from stocks.tasks import update_stock_quote


class FinnhubBucketConfigurationTests(SimpleTestCase):
    @override_settings(
        FINNHUB_BUCKET_KEY="rate_limit:finnhub:test",
        FINNHUB_BUCKET_CAPACITY=7,
        FINNHUB_BUCKET_REFILL_RATE=0.5,
        FINNHUB_BUCKET_REDIS_URL="redis://example:6379/9",
    )
    def test_get_finnhub_bucket_uses_settings(self):
        bucket = get_finnhub_bucket()

        self.assertEqual(bucket.key, "rate_limit:finnhub:test")
        self.assertEqual(bucket.capacity, 7)
        self.assertEqual(bucket.refill_rate_per_sec, 0.5)
        self.assertEqual(bucket.redis_url, "redis://example:6379/9")


class FinnhubCallSiteRateLimitTests(TestCase):
    @override_settings(FINNHUB_BUCKET_ENABLED=False)
    @patch("stocks.tasks.get_finnhub_bucket")
    @patch("stocks.tasks.fetch_finnhub_quote")
    def test_quote_bypasses_limiter_when_disabled(
        self,
        mock_fetch_finnhub_quote,
        mock_get_finnhub_bucket,
    ):
        mock_fetch_finnhub_quote.return_value = {"c": 0}
        stock = SimpleNamespace(symbol="AAPL")

        result = update_stock_quote(stock)

        self.assertIsNone(result)
        mock_get_finnhub_bucket.assert_not_called()
        mock_fetch_finnhub_quote.assert_called_once_with("AAPL")

    @override_settings(FINNHUB_BUCKET_ENABLED=True)
    @patch("stocks.tasks.fetch_finnhub_quote")
    @patch("stocks.tasks.get_finnhub_bucket")
    def test_quote_does_not_call_finnhub_when_slot_wait_times_out(
        self,
        mock_get_finnhub_bucket,
        mock_fetch_finnhub_quote,
    ):
        bucket = Mock()
        bucket.wait_for_slot.return_value = False
        mock_get_finnhub_bucket.return_value = bucket

        result = update_stock_quote(SimpleNamespace(symbol="AAPL"))

        self.assertIsNone(result)
        bucket.wait_for_slot.assert_called_once_with()
        mock_fetch_finnhub_quote.assert_not_called()

    @override_settings(FINNHUB_BUCKET_ENABLED=True)
    @patch("stocks.services.fetch_company_news")
    @patch("stocks.services.get_finnhub_bucket")
    def test_news_does_not_call_finnhub_when_slot_wait_times_out(
        self,
        mock_get_finnhub_bucket,
        mock_fetch_company_news,
    ):
        bucket = Mock()
        bucket.wait_for_slot.return_value = False
        mock_get_finnhub_bucket.return_value = bucket

        with self.assertRaisesRegex(Exception, "Rate limit wait timeout: AAPL"):
            upsert_news_for_symbol("AAPL")

        bucket.wait_for_slot.assert_called_once_with()
        mock_fetch_company_news.assert_not_called()
