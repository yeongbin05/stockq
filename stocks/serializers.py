from django.db import IntegrityError, transaction
from rest_framework import serializers, exceptions
from .models import FavoriteStock, Stock, FavoriteStock,News


class StockDetailSerializer(serializers.ModelSerializer):
    """즐겨찾기 응답용 Stock 정보"""

    latest_price = serializers.SerializerMethodField()
    change_percent = serializers.SerializerMethodField()

    class Meta:
        model = Stock
        fields = [
            "id",
            "symbol",
            "name",
            "exchange",
            "currency",
            "logo_url",
            "latest_price",
            "change_percent",
        ]

    def get_latest_price(self, obj):
        latest = obj.prices.order_by("-timestamp").first()
        if not latest:
            return None
        return float(latest.price)

    def get_change_percent(self, obj):
        latest = obj.prices.order_by("-timestamp").first()
        if not latest or latest.change_percent is None:
            return None
        return float(latest.change_percent)


class FavoriteStockSerializer(serializers.ModelSerializer):
    symbol = serializers.CharField(write_only=True)  # 클라에서 symbol 받음
    stock = StockDetailSerializer(read_only=True)  # 조회 시 stock 전체 정보 반환

    class Meta:
        model = FavoriteStock
        fields = ["id", "symbol", "stock", "created_at"]
        read_only_fields = ["id", "stock", "created_at"]

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
    is_favorite = serializers.BooleanField(
        source="is_favorite_annotated",
        default=False,
        read_only=True,
    )
    latest_price = serializers.FloatField(
        source="latest_price_annotated",
        allow_null=True,
        read_only=True,
    )
    change_percent = serializers.FloatField(
        source="latest_change_percent_annotated",
        allow_null=True,
        read_only=True,
    )

    class Meta:
        model = Stock
        fields = [
            "id",
            "symbol",
            "name",
            "exchange",
            "logo_url",
            "latest_price",
            "change_percent",
            "is_favorite",
        ]


class StockBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stock
        fields = ("symbol", "name","logo_url")

class NewsSerializer(serializers.ModelSerializer):
    stocks = StockBriefSerializer(many=True, read_only=True)

    class Meta:
        model = News
        fields = ("id", "headline", "source", "published_at",
                  "url", "canonical_url", "stocks")
