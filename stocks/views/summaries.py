from django.utils import timezone as django_timezone
from rest_framework import status, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from stocks.models import FavoriteStock, Stock, Summary
from stocks.tasks import generate_summary_for_stock


class NewsSummaryViewSet(viewsets.ViewSet):
    """
    뉴스 요약 관련 API
    - GET /api/summaries/ : 내 요약 목록 조회
    - POST /api/summaries/ : 요약 생성 요청
    - GET /api/summaries/{symbol}/ : 특정 종목 요약 조회
    """

    permission_classes = [IsAuthenticated]

    def list(self, request):
        today = django_timezone.now().date()

        favs = (
            FavoriteStock.objects.filter(user=request.user)
            .select_related("stock")
            .only("stock__id", "stock__symbol", "stock__name")
        )
        stock_ids = [f.stock_id for f in favs]

        summaries = (
            Summary.objects.filter(stock_id__in=stock_ids, date=today)
            .select_related("stock")
            .only("stock_id", "summary", "date")
        )
        summary_map = {s.stock_id: s for s in summaries}

        data = []
        for f in favs:
            s = summary_map.get(f.stock_id)
            data.append(
                {
                    "stock": {"symbol": f.stock.symbol, "name": f.stock.name},
                    "date": today.isoformat(),
                    "summary": s.summary if s else None,
                    "summary_exists": bool(s),
                }
            )

        return Response(
            {"date": today.isoformat(), "count": len(data), "summaries": data}
        )

    def create(self, request):
        """요약 생성 요청"""
        symbol = request.data.get("symbol")

        # 1) 특정 종목 요약 생성
        if symbol:
            symbol = symbol.upper()

            try:
                stock = Stock.objects.get(symbol__iexact=symbol)

                # 사용자가 해당 종목을 즐겨찾기에 등록했는지 확인
                if not FavoriteStock.objects.filter(user=request.user, stock=stock).exists():
                    return Response(
                        {"error": "해당 종목이 즐겨찾기에 등록되지 않았습니다."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

                task = generate_summary_for_stock.delay(symbol)

                return Response(
                    {
                        "message": "요약 생성 요청이 큐에 추가되었습니다.",
                        "task_id": task.id,
                        "symbol": symbol,
                    },
                    status=status.HTTP_202_ACCEPTED,
                )

            except Stock.DoesNotExist:
                return Response(
                    {"error": "해당 종목을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND
                )

        # 2) 모든 관심종목(=내 즐겨찾기) 요약 생성
        symbols = list(
            FavoriteStock.objects.filter(user=request.user)
            .select_related("stock")
            .values_list("stock__symbol", flat=True)
            .distinct()
        )

        if not symbols:
            return Response(
                {"error": "즐겨찾기 종목이 없습니다."}, status=status.HTTP_400_BAD_REQUEST
            )

        task_ids = []
        for sym in symbols:
            t = generate_summary_for_stock.delay(sym)
            task_ids.append(t.id)

        return Response(
            {
                "message": "모든 관심종목 요약 생성 요청이 큐에 추가되었습니다.",
                "task_ids": task_ids,
                "count": len(symbols),
            },
            status=status.HTTP_202_ACCEPTED,
        )

    def retrieve(self, request, symbol=None):
        if not symbol:
            return Response(
                {"error": "종목 심볼이 필요합니다."}, status=status.HTTP_400_BAD_REQUEST
            )

        symbol = symbol.upper()
        today = django_timezone.now().date()

        # 1) 종목 존재 확인
        stock = Stock.objects.filter(symbol__iexact=symbol).only("id", "symbol", "name").first()
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

        # 3) 오늘 Summary 조회
        s = Summary.objects.filter(stock=stock, date=today).only("summary", "date").first()
        if not s:
            return Response(
                {
                    "stock": {"symbol": stock.symbol, "name": stock.name},
                    "date": today.isoformat(),
                    "summary": None,
                    "summary_exists": False,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "stock": {"symbol": stock.symbol, "name": stock.name},
                "date": s.date.isoformat(),
                "summary": s.summary,
                "summary_exists": True,
            }
        )
