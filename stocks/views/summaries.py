from django.db.models import OuterRef, Subquery
from django.utils import timezone as django_timezone
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from stocks.cache import get_cached_summaries, set_cached_summaries, summary_cache_key, get_cached_summary, set_cached_summary
from stocks.models import FavoriteStock, Price, Stock, Summary


def build_stock_payload(stock, latest_price=None, change_percent=None):
    return {
        "symbol": stock.symbol,
        "name": stock.name,
        "logo_url": stock.logo_url,
        "latest_price": float(latest_price) if latest_price is not None else None,
        "change_percent": float(change_percent) if change_percent is not None else None,
    }


class NewsSummaryViewSet(viewsets.ViewSet):
    """
    저장된 뉴스 요약을 조회하는 read API입니다.

    공식 요약 생성 흐름은 SummaryJob + dispatcher + Celery worker 파이프라인이며,
    일반 사용자 요청 경로에서 LLM 요약 생성을 직접 실행하지 않습니다.

    - GET /api/stocks/summaries/ : 내 관심종목의 저장된 요약 목록 조회
    - GET /api/stocks/summaries/{symbol}/ : 특정 관심종목의 저장된 요약 조회
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        # TIME_ZONE=Asia/Seoul이어도 now().date()는 UTC 기준 날짜라 KST 자정 전후로
        # 어제/오늘이 어긋날 수 있다. localdate()로 KST 기준 날짜를 명시적으로 구한다.
        today = django_timezone.localdate()
        latest_price_qs = Price.objects.filter(
            stock_id=OuterRef("stock_id")
        ).order_by("-timestamp")

        favs = (
            FavoriteStock.objects.filter(user=request.user)
            .select_related("stock")
            .only("stock__id", "stock__symbol", "stock__name", "stock__logo_url")
            .annotate(
                latest_price=Subquery(latest_price_qs.values("price")[:1]),
                latest_change_percent=Subquery(latest_price_qs.values("change_percent")[:1]),
            )
        )
        stock_ids = [f.stock_id for f in favs]

        cache_keys_by_stock_id = {
            stock_id: summary_cache_key(stock_id, today) for stock_id in stock_ids
        }
        summary_map = get_cached_summaries(cache_keys_by_stock_id)

        missing_stock_ids = [sid for sid in stock_ids if sid not in summary_map]
        if missing_stock_ids:
            fetched = {
                s.stock_id: s.summary
                for s in Summary.objects.filter(
                    stock_id__in=missing_stock_ids, date=today
                ).only("stock_id", "summary", "date")
            }
            if fetched:
                set_cached_summaries(
                    (stock_id, today, summary) for stock_id, summary in fetched.items()
                )
            summary_map.update(fetched)

        data = []
        for f in favs:
            summary = summary_map.get(f.stock_id)
            data.append(
                {
                    "stock": build_stock_payload(
                        f.stock,
                        latest_price=f.latest_price,
                        change_percent=f.latest_change_percent,
                    ),
                    "date": today.isoformat(),
                    "summary": summary,
                    "summary_exists": summary is not None,
                }
            )

        return Response(
            {"date": today.isoformat(), "count": len(data), "summaries": data}
        )

    def retrieve(self, request, symbol=None):
        if not symbol:
            return Response(
                {"error": "종목 심볼이 필요합니다."}, status=status.HTTP_400_BAD_REQUEST
            )

        symbol = symbol.upper()
        # list()와 동일하게 KST 기준 날짜를 사용한다 (위 주석 참고).
        today = django_timezone.localdate()

        latest_price_qs = Price.objects.filter(
            stock_id=OuterRef("pk")
        ).order_by("-timestamp")
        # 1) 종목 존재 확인
        stock = (
            Stock.objects.filter(symbol__iexact=symbol)
            .only("id", "symbol", "name", "logo_url")
            .annotate(
                latest_price=Subquery(latest_price_qs.values("price")[:1]),
                latest_change_percent=Subquery(latest_price_qs.values("change_percent")[:1]),
            )
            .first()
        )
        if not stock:
            return Response(
                {"error": "해당 종목을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND
            )

        # 2) 즐겨찾기 여부 확인
        if not FavoriteStock.objects.filter(user=request.user, stock=stock).exists():
            return Response(
                {"error": "해당 종목이 즐겨찾기에 등록되지 않았습니다."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3) 오늘 Summary 조회 (cache-aside: hit이면 DB 조회 없이 반환)
        cached_summary = get_cached_summary(stock.id, today)
        if cached_summary is not None:
            return Response(
                {
                    "stock": build_stock_payload(
                        stock,
                        latest_price=stock.latest_price,
                        change_percent=stock.latest_change_percent,
                    ),
                    "date": today.isoformat(),
                    "summary": cached_summary,
                    "summary_exists": True,
                }
            )

        s = Summary.objects.filter(stock=stock, date=today).only("summary", "date").first()
        if not s:
            return Response(
                {
                    "stock": build_stock_payload(
                        stock,
                        latest_price=stock.latest_price,
                        change_percent=stock.latest_change_percent,
                    ),
                    "date": today.isoformat(),
                    "summary": None,
                    "summary_exists": False,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        set_cached_summary(stock.id, today, s.summary)

        return Response(
            {
                "stock": build_stock_payload(
                    stock,
                    latest_price=stock.latest_price,
                    change_percent=stock.latest_change_percent,
                ),
                "date": s.date.isoformat(),
                "summary": s.summary,
                "summary_exists": True,
            }
        )
