# stocks/urls.py
from django.urls import path
from .views import FavoriteStockViewSet, StockSearchViewSet

urlpatterns = [
    # 즐겨찾기 목록 조회 & 추가
    path(
        "favorites/",
        FavoriteStockViewSet.as_view({"get": "list", "post": "create"}),
        name="favorite-stock-list-create",
    ),

    # 즐겨찾기 삭제: 심볼 기준
    # 예: DELETE /api/stocks/favorites/remove/AAPL/
    path(
        "favorites/remove/<str:symbol>/",
        FavoriteStockViewSet.as_view({"delete": "remove"}),
        name="favorite-stock-remove",
    ),

    # 종목 검색
    path(
        "search/",
        StockSearchViewSet.as_view({"get": "list"}),
        name="stock-search",
    ),
]
