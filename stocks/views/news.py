from django.db.models import Prefetch
from django.utils.dateparse import parse_datetime
from rest_framework import permissions
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from stocks.models import FavoriteStock, News, Stock
from stocks.serializers import NewsSerializer
from stocks.services import upsert_news_for_symbol


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
        qs = News.objects.prefetch_related(
            Prefetch("stocks", queryset=Stock.objects.only("id", "symbol", "name"))
        ).order_by("-published_at")

        # ?tickers=AAPL,MSFT
        tickers = self.request.query_params.get("tickers")
        if tickers:
            symbols = [t.strip().upper() for t in tickers.split(",") if t.strip()]
            qs = qs.filter(stocks__symbol__in=symbols)

        # ?favorites=1  (내 즐겨찾기 종목만)
        if self.request.query_params.get("favorites") in ("1", "true", "True"):
            fav_symbols = FavoriteStock.objects.filter(user=self.request.user).values_list(
                "stock__symbol", flat=True
            )
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


class NewsIngestView(APIView):
    permission_classes = [permissions.IsAuthenticated]  # 원하면 AllowAny로 바꿔도 됨

    def post(self, request):
        symbol = request.data.get("symbol", "AAPL").upper()
        try:
            days = int(request.data.get("days", 1))
        except ValueError:
            days = 1
        res = upsert_news_for_symbol(symbol, days=days)
        return Response({"symbol": symbol, "days": days, **res})
