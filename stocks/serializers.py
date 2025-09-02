from rest_framework import serializers
from .models import FavoriteStock, Stock

class FavoriteStockSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(write_only=True)  # ✅ 클라이언트에서 symbol을 받기 위한 필드
    stock_symbol = serializers.CharField(source='stock.symbol', read_only=True)  # ✅ 응답에서 보여줄 symbol

    class Meta:
        model = FavoriteStock
        fields = ["id", "symbol", "stock_symbol"]
        read_only_fields = ["id", "stock_symbol"]

    def create(self, validated_data):
        validated_data.pop("symbol", None)  # ✅ 모델에 없는 필드 제거
        return super().create(validated_data)


class StockSearchSerializer(serializers.ModelSerializer):
    is_favorite = serializers.SerializerMethodField()  # ✅ 추가

    class Meta:
        model = Stock
        fields = ["id", "symbol", "name", "exchange", "is_favorite"]  # is_favorite 포함

    def get_is_favorite(self, obj):
        user = self.context.get('request').user
        if not user or user.is_anonymous:
            return False
        return FavoriteStock.objects.filter(user=user, stock=obj).exists()
