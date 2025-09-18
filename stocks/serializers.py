from django.db import IntegrityError, transaction
from rest_framework import serializers, exceptions
from .models import FavoriteStock, Stock, FavoriteStock


class FavoriteStockSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(write_only=True)  # 클라에서 symbol 받음
    stock_symbol = serializers.CharField(source="stock.symbol", read_only=True)

    class Meta:
        model = FavoriteStock
        fields = ["id", "symbol", "stock_symbol"]
        read_only_fields = ["id", "stock_symbol"]

    def create(self, validated_data):
        request = self.context.get("request")
        user = request.user

        # 익명 사용자 방어
        if not user or not user.is_authenticated:
            raise exceptions.NotAuthenticated("Authentication required")

        # symbol 필수 검증
        symbol = validated_data.pop("symbol", None)
        if not symbol:
            raise serializers.ValidationError({"symbol": "This field is required."})

        # Stock 존재 여부 확인
        try:
            stock = Stock.objects.get(symbol=symbol)
        except Stock.DoesNotExist:
            raise serializers.ValidationError({"symbol": f"'{symbol}' not found"})

        # 중복 등록 처리 (race condition 대비)
        try:
            with transaction.atomic():
                return FavoriteStock.objects.create(user=user, stock=stock)
        except IntegrityError:
            raise serializers.ValidationError({"detail": "Already favorited"}, code=409)


class StockSearchSerializer(serializers.ModelSerializer):
    is_favorite = serializers.SerializerMethodField()

    class Meta:
        model = Stock
        fields = ["id", "symbol", "name", "exchange", "is_favorite"]

    def get_is_favorite(self, obj):
        user = self.context.get("request").user
        if not user or user.is_anonymous:
            return False
        return FavoriteStock.objects.filter(user=user, stock=obj).exists()
