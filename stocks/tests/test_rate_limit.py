from types import SimpleNamespace
from unittest.mock import Mock, call, patch

from django.test import SimpleTestCase, TestCase, override_settings

from stocks.rate_limit import get_finnhub_bucket
from stocks.services import (
    _finnhub_backoff_seconds,
    _sleep_for_finnhub_429,
    fetch_company_news,
    upsert_news_for_symbol,
)
from stocks.tasks import fetch_finnhub_quote, update_stock_quote


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


class Finnhub429RetryTests(SimpleTestCase):
    @patch("stocks.services.random.uniform", return_value=0.25)
    def test_backoff_seconds_uses_exponential_delay_and_jitter(self, mock_uniform):
        self.assertEqual(_finnhub_backoff_seconds(2), 4.25)
        mock_uniform.assert_called_once_with(0, 0.5)

    @patch("stocks.services.time.sleep")
    @patch("stocks.services._finnhub_backoff_seconds", return_value=2.25)
    def test_sleep_helper_waits_for_calculated_backoff(
        self,
        mock_backoff_seconds,
        mock_sleep,
    ):
        sleep_seconds = _sleep_for_finnhub_429(1)

        self.assertEqual(sleep_seconds, 2.25)
        mock_backoff_seconds.assert_called_once_with(1)
        mock_sleep.assert_called_once_with(2.25)

    @override_settings(FINNHUB_API_KEY="test-key")
    @patch("stocks.services._sleep_for_finnhub_429", return_value=1.25)
    @patch("stocks.services.requests.get")
    def test_company_news_retries_after_429(
        self,
        mock_get,
        mock_sleep_for_429,
    ):
        rate_limited = Mock(status_code=429)
        success = Mock(status_code=200)
        success.json.return_value = [{"id": 1}]
        mock_get.side_effect = [rate_limited, success]

        result = fetch_company_news("AAPL")

        self.assertEqual(result, [{"id": 1}])
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep_for_429.assert_called_once_with(0)

    @override_settings(FINNHUB_API_KEY="test-key")
    @patch("stocks.tasks._sleep_for_finnhub_429", return_value=1.25)
    @patch("stocks.tasks.requests.get")
    def test_quote_retries_after_429(
        self,
        mock_get,
        mock_sleep_for_429,
    ):
        rate_limited = Mock(status_code=429)
        success = Mock(status_code=200)
        success.json.return_value = {"c": 123.45}
        mock_get.side_effect = [rate_limited, success]

        result = fetch_finnhub_quote("AAPL")

        self.assertEqual(result, {"c": 123.45})
        self.assertEqual(mock_get.call_count, 2)
        mock_sleep_for_429.assert_called_once_with(0)
        success.raise_for_status.assert_called_once_with()

    @override_settings(FINNHUB_API_KEY="test-key")
    @patch("stocks.tasks._sleep_for_finnhub_429", return_value=1.25)
    @patch("stocks.tasks.requests.get")
    def test_quote_raises_after_last_429(
        self,
        mock_get,
        mock_sleep_for_429,
    ):
        mock_get.return_value = Mock(status_code=429)

        with self.assertRaisesRegex(
            Exception,
            "Finnhub 429 Too Many Requests: AAPL",
        ):
            fetch_finnhub_quote("AAPL")

        self.assertEqual(mock_get.call_count, 5)
        self.assertEqual(
            mock_sleep_for_429.call_args_list,
            [call(0), call(1), call(2), call(3)],
        )
