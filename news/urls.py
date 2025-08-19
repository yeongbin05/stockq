# news/urls.py
from django.urls import path
from .views import NewsFeedView,NewsSummaryView

urlpatterns = [
    path("news/", NewsFeedView.as_view(), name="news-feed"),
    path("news/summary/", NewsSummaryView.as_view(), name="news-summary"),
]
