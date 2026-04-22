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
