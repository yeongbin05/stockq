from django.urls import path
from .views import FavoriteStockViewSet, StockSearchViewSet



urlpatterns = [
   
    # 즐겨찾기 목록 조회 및 추가
    path(
        "favorites/",
        FavoriteStockViewSet.as_view({"get": "list", "post": "create"}),
        name="favorite-stock-list-create",
    ),

    # 즐겨찾기 삭제 (custom action)
    path(
        "favorites/<str:pk>/remove/",
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
