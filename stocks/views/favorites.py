from django.shortcuts import get_object_or_404
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from stocks.models import FavoriteStock, Stock
from stocks.serializers import FavoriteStockSerializer


class FavoriteStockViewSet(
    viewsets.GenericViewSet,
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
):
    """
    - GET    /api/stocks/favorites/          : 내 즐겨찾기 목록
    - POST   /api/stocks/favorites/          : 즐겨찾기 추가
    - DELETE /api/stocks/favorites/{symbol}/ : 즐겨찾기 단건 삭제
    """

    serializer_class = FavoriteStockSerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "symbol"

    def get_queryset(self):
        return FavoriteStock.objects.filter(user=self.request.user).select_related("stock")

    def perform_create(self, serializer):
        user = self.request.user
        symbol = serializer.validated_data.get("symbol")

        if not symbol:
            raise ValidationError({"detail": "symbol이 필요합니다."})

        # 1. 먼저 구독 플랜 확인
        sub = user.subscriptions.filter(active=True).order_by("-start_date").first()
        plan = sub.plan if sub and sub.is_active() else "FREE"

        # 2. 현재 즐겨찾기 개수 확인
        current_count = FavoriteStock.objects.filter(user=user).count()

        # 3. 한도 초과면 Stock 테이블 조회 전에 바로 차단
        if plan in (None, "FREE") and current_count >= 3:
            raise ValidationError(
                {
                    "code": "FREE_LIMIT_EXCEEDED",
                    "detail": "무료 계정은 최대 3종목까지만 저장 가능합니다.",
                }
            )

        if plan == "PREMIUM" and current_count >= 50:
            raise ValidationError(
                {
                    "code": "PREMIUM_LIMIT_EXCEEDED",
                    "detail": "Premium 계정은 최대 50종목까지만 저장 가능합니다.",
                }
            )

        # 4. 여기까지 통과한 사람만 Stock 조회
        try:
            stock = Stock.objects.get(symbol__iexact=symbol)
        except Stock.DoesNotExist:
            raise ValidationError({"detail": f"{symbol} 종목을 찾을 수 없습니다."})

        # 5. 중복 저장 방지
        if FavoriteStock.objects.filter(user=user, stock=stock).exists():
            raise ValidationError({"detail": "이미 즐겨찾기에 등록된 종목입니다."})

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
                status=status.HTTP_404_NOT_FOUND,
            )

        fav.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
