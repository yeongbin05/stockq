from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import FavoriteStockViewSet, StockViewSet, NewsViewSet, NotificationViewSet, SummaryViewSet



router = DefaultRouter()
router.register(r"favorites", FavoriteStockViewSet, basename="favorite")
router.register(r"stocks", StockViewSet, basename="stock")
router.register(r"news", NewsViewSet, basename="news")
router.register(r"notifications", NotificationViewSet, basename="notification")
router.register(r"summaries", SummaryViewSet, basename="summary")

urlpatterns = [
    path("", include(router.urls)),
]
