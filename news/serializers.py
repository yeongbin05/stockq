# news/serializers.py
from rest_framework import serializers
from stocks.models import Stock,News

class StockBriefSerializer(serializers.ModelSerializer):
    class Meta:
        model = Stock
        fields = ("symbol", "name")

class NewsSerializer(serializers.ModelSerializer):
    stocks = StockBriefSerializer(many=True, read_only=True)

    class Meta:
        model = News
        fields = ("id", "headline", "source", "published_at",
                  "url", "canonical_url", "stocks")
