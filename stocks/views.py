import re,requests,logging
from .models import FavoriteStock, Stock,News,Summary
from .serializers import FavoriteStockSerializer, StockSearchSerializer,NewsSerializer
from .services import upsert_news_for_symbol
from .tasks import generate_summary_for_stock
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q,Prefetch
from django.http import JsonResponse
from django.utils import timezone as django_timezone
from django.utils.dateparse import parse_datetime
from django.shortcuts import get_object_or_404
from rest_framework import permissions,viewsets, mixins, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.pagination import CursorPagination,PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView



logger = logging.getLogger(__name__)
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")



class FavoriteStockViewSet(viewsets.GenericViewSet,
                           mixins.ListModelMixin,
                           mixins.CreateModelMixin):
    """
    - GET    /api/stocks/favorites/          : 내 즐겨찾기 목록
    - POST   /api/stocks/favorites/          : 즐겨찾기 추가
    - DELETE /api/stocks/favorites/{symbol}/ : 즐겨찾기 단건 삭제
    """
    serializer_class = FavoriteStockSerializer
    permission_classes = [IsAuthenticated]
    # URL에서 {symbol} 부분을 잡아내기 위해 설정
    lookup_field = "symbol" 

    def get_queryset(self):
        return (FavoriteStock.objects
                .filter(user=self.request.user)
                .select_related("stock"))

    # 생성 로직 (구독 제한 체크 포함)
    def perform_create(self, serializer):
        user = self.request.user
        symbol = serializer.validated_data.get("symbol")

        if not symbol:
            raise serializers.ValidationError({"detail": "symbol이 필요합니다."})

        # Stock 존재 여부 확인
        try:
            stock = Stock.objects.get(symbol__iexact=symbol)
        except Stock.DoesNotExist:
            raise serializers.ValidationError({"detail": f"{symbol} 종목을 찾을 수 없습니다."})

        # 중복 저장 방지
        if FavoriteStock.objects.filter(user=user, stock=stock).exists():
            raise serializers.ValidationError({"detail": "이미 즐겨찾기에 등록된 종목입니다."})

        # 🔒 구독 플랜에 따른 개수 제한 로직
        current_count = FavoriteStock.objects.filter(user=user).count()
        
        # 활성 구독 가져오기 (없으면 FREE 취급)
        # (Tip: User 모델에 get_active_subscription 같은 메서드를 만들어두면 더 깔끔합니다)
        sub = user.subscriptions.filter(active=True).order_by("-start_date").first()
        plan = sub.plan if sub and sub.is_active() else "FREE"

        if plan in (None, "FREE") and current_count >= 3:
            raise ValidationError({
                "code": "FREE_LIMIT_EXCEEDED",
                "detail": "무료 계정은 최대 3종목까지만 저장 가능합니다."
            })

        if plan == "PREMIUM" and current_count >= 50:
            raise ValidationError({
                "code": "PREMIUM_LIMIT_EXCEEDED",
                "detail": "Premium 계정은 최대 50종목까지만 저장 가능합니다."
            })

        serializer.save(user=user, stock=stock)

    # DELETE /api/stocks/favorites/{symbol}/
    # Mixin을 안 쓰고 직접 구현했으므로, lookup_field를 이용해 수동 처리
    def destroy(self, request, *args, **kwargs):
        symbol = kwargs.get("symbol", "").upper()
        
        # 1. 종목 찾기
        stock = get_object_or_404(Stock, symbol__iexact=symbol)
        
        # 2. 내 즐겨찾기에서 찾기
        fav = FavoriteStock.objects.filter(user=request.user, stock=stock).first()

        if not fav:
            return Response(
                {"detail": f"{symbol}은(는) 즐겨찾기 목록에 없습니다."}, 
                status=status.HTTP_404_NOT_FOUND
            )
            
        fav.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
class StockCursorPagination(CursorPagination):
    page_size = 20
    # Cursor 방식의 핵심: 정렬 기준이 명확해야 다음 이정표(Cursor)를 찾을 수 있습니다.
    ordering = 'id'

class StockSearchViewSet(viewsets.ViewSet):
    """
    미국 주식 종목 검색 API
    - 페이지네이션을 적용하여 수만 개의 데이터를 조각내어 가져옵니다.
    """
    permission_classes = [IsAuthenticated]
    pagination_class = StockCursorPagination

    def list(self, request):
        q = request.query_params.get("q", "").strip()
        
        if not q:
            return Response([])

        # [변경점] list()로 묶지 않고 'QuerySet' 상태를 유지합니다. 
        # 이렇게 해야 DB에서 수만 개를 한 번에 안 읽고 딱 20개만 읽어옵니다.
        queryset = Stock.objects.filter(
            Q(symbol__icontains=q) | Q(name__icontains=q)
        ).order_by("id")

        # 페이지네이터 실행
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)

        # 페이지가 있으면 페이지 데이터만 보여주고, 없으면 전체(fallback)를 보여줍니다.
        if page is not None:
            serializer = StockSearchSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)

        serializer = StockSearchSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)

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

        # 1) 내 즐겨찾기(Stock 정보 포함) 한 번에 가져오기
        favs = (
            FavoriteStock.objects
            .filter(user=request.user)
            .select_related("stock")
            .only("stock__id", "stock__symbol", "stock__name")
        )
        stock_ids = [f.stock_id for f in favs]

        # 2) 오늘 Summary를 한 번에 가져오기 (stock_id -> summary 매핑)
        summaries = (
            Summary.objects
            .filter(stock_id__in=stock_ids, date=today)
            .select_related("stock")
            .only("stock_id", "summary", "date")
        )
        summary_map = {s.stock_id: s for s in summaries}

        # 3) 즐겨찾기 목록 기준으로 응답 조합
        data = []
        for f in favs:
            s = summary_map.get(f.stock_id)
            data.append({
                "stock": {"symbol": f.stock.symbol, "name": f.stock.name},
                "date": today.isoformat(),
                "summary": s.summary if s else None,
                "summary_exists": bool(s),
            })

        return Response({"date": today.isoformat(), "count": len(data), "summaries": data})

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
                        status=status.HTTP_400_BAD_REQUEST
                    )

                task = generate_summary_for_stock.delay(symbol)

                return Response(
                    {"message": "요약 생성 요청이 큐에 추가되었습니다.", "task_id": task.id, "symbol": symbol},
                    status=status.HTTP_202_ACCEPTED
                )

            except Stock.DoesNotExist:
                return Response({"error": "해당 종목을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        # 2) 모든 관심종목(=내 즐겨찾기) 요약 생성
        symbols = list(
            FavoriteStock.objects
            .filter(user=request.user)
            .select_related("stock")
            .values_list("stock__symbol", flat=True)
            .distinct()
        )

        if not symbols:
            return Response({"error": "즐겨찾기 종목이 없습니다."}, status=status.HTTP_400_BAD_REQUEST)

        task_ids = []
        for sym in symbols:
            t = generate_summary_for_stock.delay(sym)
            task_ids.append(t.id)

        return Response(
            {"message": "모든 관심종목 요약 생성 요청이 큐에 추가되었습니다.", "task_ids": task_ids, "count": len(symbols)},
            status=status.HTTP_202_ACCEPTED
        )

    def retrieve(self, request, symbol=None):
        if not symbol:
            return Response({"error": "종목 심볼이 필요합니다."}, status=status.HTTP_400_BAD_REQUEST)

        symbol = symbol.upper()
        today = django_timezone.now().date()

        # 1) 종목 존재 확인
        stock = Stock.objects.filter(symbol__iexact=symbol).only("id", "symbol", "name").first()
        if not stock:
            return Response({"error": "해당 종목을 찾을 수 없습니다."}, status=status.HTTP_404_NOT_FOUND)

        # 2) 즐겨찾기 여부 확인
        if not FavoriteStock.objects.filter(user=request.user, stock=stock).exists():
            return Response({"error": "해당 종목이 즐겨찾기에 등록되지 않았습니다."}, status=status.HTTP_400_BAD_REQUEST)

        # 3) 오늘 Summary 조회
        s = Summary.objects.filter(stock=stock, date=today).only("summary", "date").first()
        if not s:
            return Response({
                "stock": {"symbol": stock.symbol, "name": stock.name},
                "date": today.isoformat(),
                "summary": None,
                "summary_exists": False,
            }, status=status.HTTP_404_NOT_FOUND)

        return Response({
            "stock": {"symbol": stock.symbol, "name": stock.name},
            "date": s.date.isoformat(),
            "summary": s.summary,
            "summary_exists": True,
        })

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


from django.http import HttpResponse
def xbench_test(request):
    # 3만 개를 강제로 리스트로 만들어 메모리에 올림
    stocks = list(Stock.objects.all().values_list("id", flat=True))
    return HttpResponse(f"Loaded {len(stocks)} stocks for XBench test.")