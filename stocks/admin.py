# stocks/admin.py
from django.contrib import admin
from .models import Stock, FavoriteStock, News, NewsStock, Price, Summary

@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("id", "symbol", "name", "exchange", "type", "updated_at")
    search_fields = ("symbol", "name")

@admin.register(FavoriteStock)
class FavoriteStockAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "stock", "created_at")
    search_fields = ("user__username", "user__email", "stock__symbol")

@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display = ("id", "headline", "source", "published_at")
    list_filter = ("source", "language")
    search_fields = ("headline", "url", "canonical_url")

@admin.register(NewsStock)
class NewsStockAdmin(admin.ModelAdmin):
    list_display = ("id", "news", "stock")
    list_filter = ("stock",)

@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    list_display = ("id", "stock", "price", "timestamp")
    list_filter = ("stock",)
    search_fields = ("stock__symbol",)

@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ("id", "stock", "date")
    list_filter = ("stock",)
