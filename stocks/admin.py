from django.contrib import admin
from .models import Stock, FavoriteStock, News, Price, Summary


@admin.register(Stock)
class StockAdmin(admin.ModelAdmin):
    list_display = ("symbol", "name", "exchange", "updated_at")
    search_fields = ("symbol", "name")
    list_filter = ("exchange",)


@admin.register(FavoriteStock)
class FavoriteStockAdmin(admin.ModelAdmin):
    list_display = ("user", "stock", "created_at")
    search_fields = ("user__email", "stock__symbol")


@admin.register(News)
class NewsAdmin(admin.ModelAdmin):
    list_display = ("stock", "headline", "source", "published_at")
    search_fields = ("headline", "stock__symbol")
    list_filter = ("source",)


@admin.register(Price)
class PriceAdmin(admin.ModelAdmin):
    list_display = ("stock", "price", "change_percent", "timestamp")
    search_fields = ("stock__symbol",)


@admin.register(Summary)
class SummaryAdmin(admin.ModelAdmin):
    list_display = ("stock", "date")
    search_fields = ("stock__symbol",)
