# stocks/views.py
from django.db.models import Q
from rest_framework.exceptions import ValidationError
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



