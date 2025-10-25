# stocks/urls.py
from django.urls import path
from .views import FavoriteStockViewSet, StockSearchViewSet, NewsSummaryViewSet

urlpatterns = [
    # 즐겨찾기 목록 조회 & 추가
    path(
        "favorites/",
        FavoriteStockViewSet.as_view({"get": "list", "post": "create"}),
        name="favorite-stock-list-create",
    ),
    # 즐겨찾기 단건 삭제 (RESTful)
    path(
        "favorites/<str:symbol>/",
        FavoriteStockViewSet.as_view({"delete": "destroy"}),
        name="favorite-stock-destroy",
    ),

    # 종목 검색
    path(
        "search/",
        StockSearchViewSet.as_view({"get": "list"}),
        name="stock-search",
    ),
    
    # 뉴스 요약
    path(
        "summaries/",
        NewsSummaryViewSet.as_view({"get": "list", "post": "create"}),
        name="news-summary-list-create",
    ),
    path(
        "summaries/<str:symbol>/",
        NewsSummaryViewSet.as_view({"get": "retrieve"}),
        name="news-summary-retrieve",
    ),
]
