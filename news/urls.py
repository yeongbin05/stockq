# urls.py
from django.urls import path
from .views import NewsSummaryView

urlpatterns = [
    path('news/', NewsSummaryView.as_view(), name='news-summary'),
]
