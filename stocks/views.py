# stocks/views.py
from django.db.models import Q
from rest_framework import viewsets, mixins, serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from .models import FavoriteStock, Stock
from .serializers import FavoriteStockSerializer, StockSearchSerializer


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

    def get_queryset(self):
        return (FavoriteStock.objects
                .filter(user=self.request.user)
                .select_related("stock"))

    def perform_create(self, serializer):
        user = self.request.user
        stock_id = self.request.data.get("stock_id")
        symbol = self.request.data.get("symbol")

        if not stock_id and not symbol:
            raise serializers.ValidationError({"detail": "stock_id 또는 symbol 중 하나가 필요합니다."})

        try:
            if stock_id:
                stock = Stock.objects.get(id=stock_id)
            else:
                stock = Stock.objects.get(symbol__iexact=symbol)
        except Stock.DoesNotExist:
            raise serializers.ValidationError({"detail": "해당 종목이 존재하지 않습니다."})

        if FavoriteStock.objects.filter(user=user, stock=stock).exists():
            raise serializers.ValidationError({"detail": "이미 즐겨찾기에 등록된 종목입니다."})

        serializer.save(user=user, stock=stock)

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

        exact = list(Stock.objects.filter(symbol__iexact=q))
        partial = list(Stock.objects.filter(
            Q(symbol__icontains=q) | Q(name__icontains=q)
        ).exclude(id__in=[s.id for s in exact]))

        queryset = exact + partial
        serializer = StockSearchSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)
