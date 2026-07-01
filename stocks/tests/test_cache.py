from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from stocks.cache import (
    get_cached_summaries,
    get_cached_summary,
    set_cached_summaries,
    set_cached_summary,
    summary_cache_key,
)

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


@override_settings(CACHES=LOCMEM_CACHES)
class SummaryCacheRoundTripTests(SimpleTestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

    def test_set_then_get_returns_cached_summary(self):
        summary_data = {"ticker": "AAPL", "news_summary": ["beat estimates"]}

        set_cached_summary(1, date(2026, 6, 21), summary_data)

        self.assertEqual(
            get_cached_summary(1, date(2026, 6, 21)),
            summary_data,
        )

    def test_get_returns_none_when_not_cached(self):
        self.assertIsNone(get_cached_summary(1, date(2026, 6, 21)))

    def test_cache_key_is_scoped_by_stock_and_date(self):
        set_cached_summary(1, date(2026, 6, 21), {"a": 1})

        self.assertIsNone(get_cached_summary(1, date(2026, 6, 22)))
        self.assertIsNone(get_cached_summary(2, date(2026, 6, 21)))

    def test_set_cached_summaries_and_get_cached_summaries_round_trip(self):
        today = date(2026, 6, 21)
        set_cached_summaries(
            [
                (1, today, {"ticker": "AAPL"}),
                (2, today, {"ticker": "MSFT"}),
            ]
        )

        keys_by_stock_id = {
            1: summary_cache_key(1, today),
            2: summary_cache_key(2, today),
            3: summary_cache_key(3, today),
        }
        result = get_cached_summaries(keys_by_stock_id)

        self.assertEqual(
            result,
            {1: {"ticker": "AAPL"}, 2: {"ticker": "MSFT"}},
        )

    def test_get_cached_summaries_with_empty_input_does_not_hit_cache(self):
        self.assertEqual(get_cached_summaries({}), {})

    def test_set_cached_summaries_with_empty_input_is_noop(self):
        set_cached_summaries([])  # 예외 없이 통과해야 함


class SummaryCacheRedisFailureFallbackTests(SimpleTestCase):
    """Redis 장애 시에도 캐시 계층이 예외를 삼키고 '미스'로 처리하는지 검증."""

    @patch("stocks.cache.cache.get", side_effect=ConnectionError("redis down"))
    def test_get_cached_summary_returns_none_on_redis_error(self, mock_get):
        result = get_cached_summary(1, date(2026, 6, 21))

        self.assertIsNone(result)
        mock_get.assert_called_once()

    @patch("stocks.cache.cache.set", side_effect=ConnectionError("redis down"))
    def test_set_cached_summary_does_not_raise_on_redis_error(self, mock_set):
        set_cached_summary(1, date(2026, 6, 21), {"a": 1})

        mock_set.assert_called_once()

    @patch("stocks.cache.cache.get_many", side_effect=ConnectionError("redis down"))
    def test_get_cached_summaries_returns_empty_dict_on_redis_error(self, mock_get_many):
        result = get_cached_summaries({1: summary_cache_key(1, date(2026, 6, 21))})

        self.assertEqual(result, {})

    @patch("stocks.cache.cache.set_many", side_effect=ConnectionError("redis down"))
    def test_set_cached_summaries_does_not_raise_on_redis_error(self, mock_set_many):
        set_cached_summaries([(1, date(2026, 6, 21), {"a": 1})])

        mock_set_many.assert_called_once()
