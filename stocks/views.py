# stocks/views.py
from django.db.models import Q
from rest_framework.exceptions import ValidationError
from rest_framework import viewsets, mixins, serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from rest_framework.pagination import CursorPagination
from datetime import datetime, timezone
from django.utils import timezone as django_timezone

from .models import FavoriteStock, Stock, DailyUserNews
from .serializers import FavoriteStockSerializer, StockSearchSerializer
from .tasks import generate_news_summary_with_openai


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



