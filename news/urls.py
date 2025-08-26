# news/urls.py
from django.urls import path
from .views import NewsIngestView,NewsFeedView,NewsSummaryView

urlpatterns = [
    path("ingest/", NewsIngestView.as_view()), 
    path("news/", NewsFeedView.as_view(), name="news-feed"),
    path("news/summary/", NewsSummaryView.as_view(), name="news-summary"),
]
