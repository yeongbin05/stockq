from datetime import datetime, timezone as dt_timezone, date as date_cls
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from stocks.cache import get_cached_summary, set_cached_summary
from stocks.models import FavoriteStock, Stock, Summary

LOCMEM_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


@override_settings(CACHES=LOCMEM_CACHES)
class SummaryRetrieveCacheAsideTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

        User = get_user_model()
        self.user = User.objects.create_user(email="cache-test@example.com", password="test1234")
        self.stock = Stock.objects.create(symbol="AAPL", name="Apple")
        FavoriteStock.objects.create(user=self.user, stock=self.stock)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # stocks/views/summaries.py와 동일하게 KST 기준 날짜를 사용한다.
        self.today = timezone.localdate()
        self.summary_payload = {"ticker": "AAPL", "news_summary": ["beat estimates"]}

    def test_retrieve_on_cache_miss_reads_db_and_populates_cache(self):
        Summary.objects.create(stock=self.stock, date=self.today, summary=self.summary_payload)

        response = self.client.get(
            reverse("news-summary-retrieve", kwargs={"symbol": "AAPL"})
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"], self.summary_payload)
        self.assertEqual(
            get_cached_summary(self.stock.id, self.today),
            self.summary_payload,
        )

    def test_retrieve_on_cache_hit_does_not_query_summary_table(self):
        set_cached_summary(self.stock.id, self.today, self.summary_payload)

        with patch(
            "stocks.views.summaries.Summary.objects.filter"
        ) as mock_filter:
            response = self.client.get(
                reverse("news-summary-retrieve", kwargs={"symbol": "AAPL"})
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["summary"], self.summary_payload)
        self.assertTrue(response.data["summary_exists"])
        mock_filter.assert_not_called()

    def test_retrieve_returns_404_and_does_not_cache_when_summary_missing(self):
        response = self.client.get(
            reverse("news-summary-retrieve", kwargs={"symbol": "AAPL"})
        )

        self.assertEqual(response.status_code, 404)
        self.assertIsNone(get_cached_summary(self.stock.id, self.today))


@override_settings(CACHES=LOCMEM_CACHES)
class SummaryListCacheAsideTests(TestCase):
    def setUp(self):
        from django.core.cache import cache

        cache.clear()

        User = get_user_model()
        self.user = User.objects.create_user(email="cache-list@example.com", password="test1234")
        self.aapl = Stock.objects.create(symbol="AAPL", name="Apple")
        self.msft = Stock.objects.create(symbol="MSFT", name="Microsoft")
        FavoriteStock.objects.create(user=self.user, stock=self.aapl)
        FavoriteStock.objects.create(user=self.user, stock=self.msft)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # stocks/views/summaries.py와 동일하게 KST 기준 날짜를 사용한다.
        self.today = timezone.localdate()

    def test_list_populates_cache_then_second_call_skips_db_for_summaries(self):
        Summary.objects.create(
            stock=self.aapl, date=self.today, summary={"ticker": "AAPL"}
        )

        first_response = self.client.get(reverse("news-summary-list"))
        self.assertEqual(first_response.status_code, 200)

        with patch("stocks.views.summaries.Summary.objects.filter") as mock_filter:
            second_response = self.client.get(reverse("news-summary-list"))

        self.assertEqual(second_response.status_code, 200)
        # AAPL의 Summary는 캐시에 있으니 DB는 캐시 미스인 MSFT 한 종목만 조회해야 함
        mock_filter.assert_called_once_with(
            stock_id__in=[self.msft.id], date=self.today
        )

        summaries_by_symbol = {
            item["stock"]["symbol"]: item for item in second_response.data["summaries"]
        }
        self.assertEqual(summaries_by_symbol["AAPL"]["summary"], {"ticker": "AAPL"})
        self.assertTrue(summaries_by_symbol["AAPL"]["summary_exists"])
        self.assertIsNone(summaries_by_symbol["MSFT"]["summary"])
        self.assertFalse(summaries_by_symbol["MSFT"]["summary_exists"])


@override_settings(CACHES=LOCMEM_CACHES)
class SummaryRetrieveLocaldateBoundaryTests(TestCase):
    """
    TIME_ZONE=Asia/Seoul이어도 timezone.now()는 UTC datetime을 반환하므로
    now().date()는 UTC 기준 날짜가 된다. UTC 15:00~23:59(=KST 00:00~08:59) 구간에는
    UTC 날짜와 KST 날짜가 하루 어긋난다. 뷰가 today를 localdate()로 계산하는지를
    이 경계 순간을 고정해서 검증한다.
    """

    def setUp(self):
        from django.core.cache import cache

        cache.clear()

        User = get_user_model()
        self.user = User.objects.create_user(email="boundary-test@example.com", password="test1234")
        self.stock = Stock.objects.create(symbol="AAPL", name="Apple")
        FavoriteStock.objects.create(user=self.user, stock=self.stock)

        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

        # UTC 2026-06-21 16:30 == KST 2026-06-22 01:30 → UTC 날짜와 KST 날짜가 다른 순간.
        # MagicMock이 아니라 실제 datetime을 리턴값으로 줘야 auto_now_add 같은
        # 모델 필드 저장 로직이 깨지지 않는다.
        self.fixed_utc_now = datetime(2026, 6, 21, 16, 30, tzinfo=dt_timezone.utc)
        self.utc_date = date_cls(2026, 6, 21)
        self.kst_date = date_cls(2026, 6, 22)

    def test_retrieve_uses_kst_date_not_utc_date_near_midnight_boundary(self):
        with patch("django.utils.timezone.now", return_value=self.fixed_utc_now):
            Summary.objects.create(
                stock=self.stock, date=self.kst_date, summary={"ticker": "AAPL"}
            )

            response = self.client.get(
                reverse("news-summary-retrieve", kwargs={"symbol": "AAPL"})
            )

            cached_on_kst_date = get_cached_summary(self.stock.id, self.kst_date)
            cached_on_utc_date = get_cached_summary(self.stock.id, self.utc_date)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["date"], self.kst_date.isoformat())
        self.assertEqual(response.data["summary"], {"ticker": "AAPL"})

        # 캐시도 KST 날짜 키로 채워져야 하고, UTC 날짜 키에는 아무 것도 없어야 한다.
        self.assertEqual(cached_on_kst_date, {"ticker": "AAPL"})
        self.assertIsNone(cached_on_utc_date)

    def test_list_uses_kst_date_not_utc_date_near_midnight_boundary(self):
        with patch("django.utils.timezone.now", return_value=self.fixed_utc_now):
            Summary.objects.create(
                stock=self.stock, date=self.kst_date, summary={"ticker": "AAPL"}
            )

            response = self.client.get(reverse("news-summary-list"))

            cached_on_kst_date = get_cached_summary(self.stock.id, self.kst_date)
            cached_on_utc_date = get_cached_summary(self.stock.id, self.utc_date)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["date"], self.kst_date.isoformat())

        summary_item = response.data["summaries"][0]
        self.assertEqual(summary_item["summary"], {"ticker": "AAPL"})
        self.assertTrue(summary_item["summary_exists"])

        self.assertEqual(cached_on_kst_date, {"ticker": "AAPL"})
        self.assertIsNone(cached_on_utc_date)
