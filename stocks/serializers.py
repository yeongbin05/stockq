from rest_framework import serializers
from .models import FavoriteStock, Stock, News, Price, Summary, Notification

class FavoriteStockSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source='stock.symbol', read_only=True)

    class Meta:
        model = FavoriteStock
        fields = ["id", "stock", "symbol"]
        extra_kwargs = {
            'stock': {'required': False}
        }

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


class NewsSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source='stock.symbol', read_only=True)

    class Meta:
        model = News
        fields = [
            "id",
            "symbol",
            "headline",
            "url",
            "source",
            "published_at",
        ]


class PriceSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source='stock.symbol', read_only=True)

    class Meta:
        model = Price
        fields = [
            "id",
            "symbol",
            "price",
            "change_percent",
            "timestamp",
        ]


class SummarySerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source='stock.symbol', read_only=True)

    class Meta:
        model = Summary
        fields = [
            "id",
            "symbol",
            "summary",
            "recommendations",
            "date",
        ]


class NotificationSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(source='stock.symbol', read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "symbol",
            "message",
            "is_read",
            "created_at",
        ]
