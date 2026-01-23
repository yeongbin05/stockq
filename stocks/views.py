import re,requests,logging
from .models import FavoriteStock, Stock, DailyUserNews,News
from .serializers import FavoriteStockSerializer, StockSearchSerializer,NewsSerializer
from .tasks import generate_news_summary_with_openai
from .services import upsert_news_for_symbol

from decimal import Decimal
from datetime import datetime, timezone,timedelta

from rest_framework import permissions,viewsets, mixins, serializers, status
from rest_framework.exceptions import ValidationError
from rest_framework.generics import ListAPIView
from rest_framework.pagination import CursorPagination,PageNumberPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


from django.db.models import Q,Prefetch
from django.utils import timezone as django_timezone
from django.shortcuts import get_object_or_404
from django.conf import settings
from django.http import JsonResponse
from django.views import View
from django.core.cache import cache
from django.utils.dateparse import parse_datetime




logger = logging.getLogger(__name__)
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,10}$")
def _get_yesterday_change_percent(symbol: str, api_key: str, debug: bool = False) -> dict:
    """
    '어제 하루 등락률' 계산:
    1) 우선 Finnhub /stock/candle (일봉)에서 최근 거래일 2개 close로 계산
    2) 실패/부족 시 폴백: /quote.pc(전일 종가) + candle의 마지막 close로 계산
       - 주말/휴장에도 잘 동작 (예: 금 close vs 목 pc)
    반환:
      close, prev_close, yesterday_change_percent, date_utc[, debug]
    """
    # ---- 1) 일봉 2개 시도 (정공법)
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(days=14)  # 휴장 대비 버퍼
    to_ts = int(to_dt.timestamp())
    from_ts = int(from_dt.timestamp())

    candle_url = (
        "https://finnhub.io/api/v1/stock/candle"
        f"?symbol={symbol}&resolution=D&from={from_ts}&to={to_ts}&token={api_key}"
    )
    try:
        r = requests.get(candle_url, timeout=6)
        r.raise_for_status()
        j = r.json()  # { s:"ok"|"no_data", t:[...], c:[...], ... }
        c_status = j.get("s")
        t_list = j.get("t") or []
        c_list = j.get("c") or []

        if c_status == "ok" and len(c_list) >= 2:
            close = Decimal(str(c_list[-1]))
            prev_close = Decimal(str(c_list[-2]))
            pct = None if prev_close == 0 else ((close - prev_close) / prev_close * Decimal("100")).quantize(Decimal("0.01"))
            last_ts = datetime.fromtimestamp(t_list[-1], tz=timezone.utc) if t_list else None

            out = {
                "close": str(close),
                "prev_close": str(prev_close),
                "yesterday_change_percent": None if pct is None else str(pct),
                "date_utc": last_ts.date().isoformat() if last_ts else None,
            }
            if debug:
                out["debug"] = {"path": "candle-2closes"}
            return out
    except Exception as e:
        # 계속 폴백 진행
        if debug:
            c_status = f"exception:{e}"

    # ---- 2) 폴백: /quote.pc + candle 마지막 close
    quote_url = f"https://finnhub.io/api/v1/quote?symbol={symbol}&token={api_key}"
    try:
        qr = requests.get(quote_url, timeout=5)
        qr.raise_for_status()
        q = qr.json()  # { c, pc, d, dp, ... }
        pc = q.get("pc", None)

        # candle에서 최소 1개 close는 받아와 보자 (최신 종가)
        # (위에서 받은 j/t_list/c_list 재사용 시도, 없으면 다시 호출)
        if not locals().get("j"):
            r2 = requests.get(candle_url, timeout=6)
            r2.raise_for_status()
            j = r2.json()
            t_list = j.get("t") or []
            c_list = j.get("c") or []

        if pc is not None and isinstance(c_list, list) and len(c_list) >= 1:
            close = Decimal(str(c_list[-1]))
            prev_close = Decimal(str(pc))
            pct = None if prev_close == 0 else ((close - prev_close) / prev_close * Decimal("100")).quantize(Decimal("0.01"))
            last_ts = datetime.fromtimestamp(t_list[-1], tz=timezone.utc) if t_list else None

            out = {
                "close": str(close),
                "prev_close": str(prev_close),
                "yesterday_change_percent": None if pct is None else str(pct),
                "date_utc": last_ts.date().isoformat() if last_ts else None,
            }
            if debug:
                out["debug"] = {"path": "fallback-quote-pc", "candle_status": j.get("s")}
            return out
    except Exception as e:
        if debug:
            return {
                "close": None, "prev_close": None,
                "yesterday_change_percent": None, "date_utc": None,
                "debug": {"error": str(e), "candle_status": locals().get("c_status")}
            }

    # ---- 최종 실패
    out = {"close": None, "prev_close": None, "yesterday_change_percent": None, "date_utc": None}
    if debug:
        out["debug"] = {"path": "failed", "candle_status": locals().get("c_status")}
    return out



class FavoriteStockViewSet(viewsets.GenericViewSet,
                           mixins.ListModelMixin,
                           mixins.CreateModelMixin):
    """
    - GET    /api/stocks/favorites/           : 내 즐겨찾기 목록
    - POST   /api/stocks/favorites/           : 즐겨찾기 추가 (stock_id 또는 symbol 허용)
    - DELETE /api/stocks/favorites/{symbol}/  : 즐겨찾기 단건 삭제
    """
    serializer_class = FavoriteStockSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "symbol" 
    
    def get_queryset(self):
        return (FavoriteStock.objects
                .filter(user=self.request.user)
                .select_related("stock"))


    def perform_create(self, serializer):
        user = self.request.user
        symbol = serializer.validated_data.get("symbol")  # ✅ 여기로 변경

        if not symbol:
            raise serializers.ValidationError({"detail": "symbol이 필요합니다."})

        try:
            stock = Stock.objects.get(symbol__iexact=symbol)
        except Stock.DoesNotExist:
            raise serializers.ValidationError({"detail": "해당 종목이 존재하지 않습니다."})

        if FavoriteStock.objects.filter(user=user, stock=stock).exists():
            raise serializers.ValidationError({"detail": "이미 즐겨찾기에 등록된 종목입니다."})

        # 🔒 구독 제한
        sub = user.subscriptions.filter(active=True).order_by("-start_date").first()
        plan = sub.plan if sub and sub.is_active() else "FREE"
        current_count = FavoriteStock.objects.filter(user=user).count()

        if plan in (None, "FREE") and current_count >= 3:
            raise ValidationError({
                "code": "FREE_LIMIT_EXCEEDED",
                "message": "무료 계정은 최대 3종목까지만 저장 가능합니다."
            })

        if plan == "PREMIUM" and current_count >= 50:
            raise ValidationError({
                "code": "PREMIUM_LIMIT_EXCEEDED",
                "message": "Premium 계정은 최대 50종목까지만 저장 가능합니다."
            })

        fav = serializer.save(user=user, stock=stock)
        print(">>> saved", fav.id)



    def create(self, request, *args, **kwargs):
        resp = super().create(request, *args, **kwargs)
        resp.status_code = status.HTTP_201_CREATED
        return resp

    # RESTful 단건 삭제: DELETE /api/stocks/favorites/{symbol}/
    def destroy(self, request, symbol=None):
        symbol = (symbol or "").upper()
        stock = get_object_or_404(Stock, symbol__iexact=symbol)
        fav = FavoriteStock.objects.filter(user=request.user, stock=stock).first()

        if not fav:
            return Response({"detail": f"{symbol}은(는) 즐겨찾기 목록에 없음"}, status=status.HTTP_404_NOT_FOUND)
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
        """사용자의 모든 요약 조회"""
        today = django_timezone.now().date()
        
        summaries = DailyUserNews.objects.filter(
            user=request.user,
            date=today
        ).select_related('stock').order_by('-created_at')
        
        data = []
        for summary in summaries:
            data.append({
                'id': summary.id,
                'stock': {
                    'symbol': summary.stock.symbol,
                    'name': summary.stock.name
                },
                'summary': summary.summary,
                'date': summary.date,
                'created_at': summary.created_at
            })
        
        return Response({
            'date': today,
            'summaries': data,
            'count': len(data)
        })

    def create(self, request):
        """요약 생성 요청"""
        symbol = request.data.get('symbol')
        
        if symbol:
            # 특정 종목 요약 생성
            try:
                stock = Stock.objects.get(symbol__iexact=symbol)
                # 사용자가 해당 종목을 즐겨찾기에 등록했는지 확인
                if not FavoriteStock.objects.filter(user=request.user, stock=stock).exists():
                    return Response({
                        'error': '해당 종목이 즐겨찾기에 등록되지 않았습니다.'
                    }, status=status.HTTP_400_BAD_REQUEST)
                
                # Celery 태스크 실행
                task = generate_news_summary_with_openai.delay(request.user.id, symbol)
                
                return Response({
                    'message': '요약 생성 요청이 큐에 추가되었습니다.',
                    'task_id': task.id,
                    'symbol': symbol
                }, status=status.HTTP_202_ACCEPTED)
                
            except Stock.DoesNotExist:
                return Response({
                    'error': '해당 종목을 찾을 수 없습니다.'
                }, status=status.HTTP_404_NOT_FOUND)
        else:
            # 모든 관심종목 요약 생성
            task = generate_news_summary_with_openai.delay(request.user.id)
            
            return Response({
                'message': '모든 관심종목 요약 생성 요청이 큐에 추가되었습니다.',
                'task_id': task.id
            }, status=status.HTTP_202_ACCEPTED)

    def retrieve(self, request, symbol=None):
        """특정 종목의 요약 조회"""
        if not symbol:
            return Response({
                'error': '종목 심볼이 필요합니다.'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            stock = Stock.objects.get(symbol__iexact=symbol)
        except Stock.DoesNotExist:
            return Response({
                'error': '해당 종목을 찾을 수 없습니다.'
            }, status=status.HTTP_404_NOT_FOUND)
        
        today = django_timezone.now().date()
        
        try:
            summary = DailyUserNews.objects.get(
                user=request.user,
                stock=stock,
                date=today
            )
            
            return Response({
                'stock': {
                    'symbol': stock.symbol,
                    'name': stock.name
                },
                'summary': summary.summary,
                'date': summary.date,
                'created_at': summary.created_at
            })
            
        except DailyUserNews.DoesNotExist:
            return Response({
                'error': '해당 종목의 오늘 요약이 없습니다.',
                'stock': {
                    'symbol': stock.symbol,
                    'name': stock.name
                },
                'date': today
            }, status=status.HTTP_404_NOT_FOUND)




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
        nocache = request.GET.get("nocache") in ("1", "true", "True")
        cache_key = f"news:summary:{symbol}:{days}:{source_filter}:{limit}:{from_date}:{to_date}:v2"  # ← v2로 강제 무효화
        if not nocache:
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
        # 9.x) 전일 대비 등락률 계산 추가
    
        try:
            debug_flag = request.GET.get("debug") in ("1", "true", "True")
            eod = _get_yesterday_change_percent(symbol, api_key, debug=debug_flag)
        except Exception as e:
            logger.warning("EOD change calc failed for %s: %s", symbol, e)
            eod = {"yesterday_change_percent": None, "close": None, "prev_close": None, "date_utc": None}


        payload = {
            "symbol": symbol,
            "from": str(from_date),
            "to": str(to_date),
            "source": source_filter,
            "count": len(items),
            "items": items,
            "yesterday_change_percent": eod["yesterday_change_percent"],
            "close": eod["close"],
            "prev_close": eod["prev_close"],
            "eod_date_utc": eod["date_utc"],
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
