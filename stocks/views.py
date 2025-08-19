# stocks/views.py
from django.db.models import Q
from rest_framework import viewsets, mixins, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.status import HTTP_201_CREATED, HTTP_404_NOT_FOUND

from .models import FavoriteStock, Stock
from .serializers import FavoriteStockSerializer, StockSearchSerializer


class FavoriteStockViewSet(viewsets.GenericViewSet,
                           mixins.ListModelMixin,
                           mixins.CreateModelMixin):
    """
    - GET  /api/stocks/favorites/            : 내 즐겨찾기 목록
    - POST /api/stocks/favorites/            : 즐겨찾기 추가 (stock_id 또는 symbol 허용)
    - DELETE /api/stocks/favorites/remove/<symbol>/ : 즐겨찾기 삭제
    """
    serializer_class = FavoriteStockSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # DRF 인증이 보장하는 request.user 사용. 불필요한 토큰 수동 파싱 제거.
        return (FavoriteStock.objects
                .filter(user=self.request.user)
                .select_related("stock"))

    def perform_create(self, serializer):
        user = self.request.user
        stock_id = self.request.data.get("stock_id")
        symbol = self.request.data.get("symbol")

        if not stock_id and not symbol:
            raise serializers.ValidationError({"detail": "stock_id 또는 symbol 중 하나가 필요합니다."})

        # stock_id 우선, 없으면 symbol로 조회
        try:
            if stock_id:
                stock = Stock.objects.get(id=stock_id)
            else:
                stock = Stock.objects.get(symbol__iexact=symbol)
        except Stock.DoesNotExist:
            raise serializers.ValidationError({"detail": "해당 종목이 존재하지 않습니다."})

        # 중복 방지
        if FavoriteStock.objects.filter(user=user, stock=stock).exists():
            raise serializers.ValidationError({"detail": "이미 즐겨찾기에 등록된 종목입니다."})

        serializer.save(user=user, stock=stock)

    def create(self, request, *args, **kwargs):
        # 생성 시 응답 201 + 현재 serializer 스키마로 반환
        resp = super().create(request, *args, **kwargs)
        resp.status_code = HTTP_201_CREATED
        return resp

    @action(detail=False, methods=["delete"], url_path=r"remove/(?P<symbol>[^/]+)")
    def remove(self, request, symbol=None):
        """
        DELETE /api/stocks/favorites/remove/<symbol>/
        """
        instance = self.get_queryset().filter(stock__symbol__iexact=symbol).first()
        if not instance:
            return Response({"message": f"{symbol}은(는) 즐겨찾기 목록에 없음"}, status=HTTP_404_NOT_FOUND)
        instance.delete()
        return Response({"message": f"{symbol} 즐겨찾기 삭제됨"})


class StockSearchViewSet(viewsets.ViewSet):
    """
    - GET /api/stocks/search/?q=apple
      정확 일치(symbol) 우선, 다음 부분 일치(symbol/name)
    """
    permission_classes = [IsAuthenticated]

    def list(self, request):
        q = request.query_params.get("q", "").strip()
        if not q:
            return Response([])

        # 1) symbol 정확 일치
        exact = list(Stock.objects.filter(symbol__iexact=q))

        # 2) symbol/name 부분 일치
        partial = list(Stock.objects.filter(
            Q(symbol__icontains=q) | Q(name__icontains=q)
        ).exclude(id__in=[s.id for s in exact]))

        queryset = exact + partial

        serializer = StockSearchSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)
