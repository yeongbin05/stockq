# news/views.py
import re
import logging
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings
from django.http import JsonResponse
from django.views import View
from django.core.cache import cache

from django.utils.dateparse import parse_datetime
from django.db.models import Prefetch

from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.pagination import PageNumberPagination

from stocks.models import News, Stock, FavoriteStock  # ✅ stocks에서 News/Stock/FavoriteStock 사용
from .serializers import NewsSerializer

logger = logging.getLogger(__name__)
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")


# -------------------------------
# 외부 API 기반 NewsSummary (실시간)
# -------------------------------
class NewsSummaryView(View):
    def get(self, request):
        # 1) 심볼 검증
        symbol = request.GET.get("symbol", "AAPL").upper()
        if not SYMBOL_RE.match(symbol):
            return JsonResponse({"error": "Invalid symbol format."}, status=400)

        # 2) 기간 파라미터 (기본 1일)
        try:
            days = int(request.GET.get("days", "1"))
            if days <= 0 or days > 7:
                raise ValueError
        except ValueError:
            return JsonResponse({"error": "days must be an integer between 1 and 7."}, status=400)

        # 3) 소스 필터
        source_filter = request.GET.get("source", "yahoo").lower().strip()

        # 4) 상한(limit)
        try:
            limit = int(request.GET.get("limit", "20"))
            limit = max(1, min(limit, 100))
        except ValueError:
            limit = 20

        # 5) 날짜 범위
        to_date = datetime.now(timezone.utc).date()
        from_date = to_date - timedelta(days=days)

        # 6) API 키 확인
        api_key = getattr(settings, "FINNHUB_API_KEY", None)
        if not api_key:
            logger.error("FINNHUB_API_KEY is missing in settings.")
            return JsonResponse({"error": "Server API key misconfiguration."}, status=500)

        # 7) 캐시 확인
        cache_key = f"news:summary:{symbol}:{days}:{source_filter}:{limit}:{from_date}:{to_date}"
        cached = cache.get(cache_key)
        if cached:
            cached["cached"] = True
            return JsonResponse(cached)

        # 8) Finnhub 요청
        url = (
            "https://finnhub.io/api/v1/company-news"
            f"?symbol={symbol}&from={from_date}&to={to_date}&token={api_key}"
        )
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            raw = resp.json()
            if not isinstance(raw, list):
                raise ValueError("Unexpected response shape")
        except Exception as e:
            logger.exception("Finnhub request failed: %s", e)
            return JsonResponse({"error": "Failed to fetch upstream news."}, status=502)

        # 9) 소스 필터 + 필드 추출
        items = []
        for item in raw:
            if source_filter and item.get("source", "").lower() != source_filter:
                continue
            items.append({
                "headline": item.get("headline", ""),
                "summary": item.get("summary", ""),
                "url": item.get("url", ""),
                "source": item.get("source", ""),
                "datetime": item.get("datetime"),
            })
            if len(items) >= limit:
                break

        payload = {
            "symbol": symbol,
            "from": str(from_date),
            "to": str(to_date),
            "source": source_filter,
            "count": len(items),
            "items": items,
            "cached": False,
        }

        # 10) 캐시에 저장
        cache.set(cache_key, payload, timeout=300)
        return JsonResponse(payload)


# -------------------------------
# DB 기반 NewsFeed (DRF + Serializer 사용)
# -------------------------------
class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

class NewsFeedView(ListAPIView):
    """
    GET /api/news/
    - 파라미터:
      * tickers=AAPL,MSFT
      * favorites=1
      * since=2025-08-01T00:00:00Z
      * until=2025-08-19T23:59:59Z
    """
    permission_classes = [IsAuthenticated]
    serializer_class = NewsSerializer
    pagination_class = StandardPagination

    def get_queryset(self):
        qs = (
            News.objects.prefetch_related(
                Prefetch("stocks", queryset=Stock.objects.only("id", "symbol", "name"))
            )
            .order_by("-published_at")
        )

        # ?tickers=AAPL,MSFT
        tickers = self.request.query_params.get("tickers")
        if tickers:
            symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
            qs = qs.filter(stocks__symbol__in=symbols)

        # ?favorites=1  (내 즐겨찾기 종목만)
        if self.request.query_params.get("favorites") in ("1", "true", "True"):
            fav_symbols = FavoriteStock.objects.filter(
                user=self.request.user
            ).values_list("stock__symbol", flat=True)
            qs = qs.filter(stocks__symbol__in=list(fav_symbols))

        # ?since, ?until (ISO8601)
        since = self.request.query_params.get("since")
        if since:
            dt = parse_datetime(since)
            if dt:
                qs = qs.filter(published_at__gte=dt)

        until = self.request.query_params.get("until")
        if until:
            dt = parse_datetime(until)
            if dt:
                qs = qs.filter(published_at__lte=dt)

        return qs.distinct()
