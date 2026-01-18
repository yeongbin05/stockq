"""
URL configuration for stockq project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.urls import path,include

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from django.db import connection
import redis
import requests
from decouple import config
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView,SpectacularRedocView
@api_view(["GET"])
@permission_classes([AllowAny])
def health(request):
    return Response({"status": "ok"})

# ↓ 추가: 인증 확인용 핑 (전역 IsAuthenticated 적용됨)
@api_view(["GET"])
def ping(request):
    return Response({"message": "pong", "user": request.user.username})

@api_view(["GET"])
@permission_classes([AllowAny])
def readiness(request):
    result = {}

    # DB 체크
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1;")
            cursor.fetchone()
        result["database"] = "ok"
    except Exception as e:
        result["database"] = f"fail: {str(e)}"

    # Redis 체크
    try:
        r = redis.Redis.from_url(config("REDIS_URL"))
        r.ping()
        result["redis"] = "ok"
    except Exception as e:
        result["redis"] = f"fail: {str(e)}"

    # Finnhub 체크
    try:
        token = config("FINNHUB_API_KEY")
        resp = requests.get(
            "https://finnhub.io/api/v1/forex/exchange",
            params={"token": token},
            timeout=0.8
        )
        if resp.status_code == 200:
            result["finnhub"] = "ok"
        else:
            result["finnhub"] = f"fail: status {resp.status_code}"
    except Exception as e:
        result["finnhub"] = f"fail: {str(e)}"

    # TODO: 운영 환경에서는 외부 API 쿼터 낭비 방지를 위해
    #       - 백그라운드 태스크(예: 10분/1시간마다 ping)
    #       - 캐시에 결과 저장 후 readiness에서는 캐시만 반환
    #       으로 개선할 것.

    return Response(result)


urlpatterns = [
    path('admin/', admin.site.urls),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),     # JSON
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema")), # Swagger UI

    path("api/health/", health),   # 공개
    path("api/ping/", ping),       # 인증 필요
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema")),
    # auth랑 users로 분리
    path("api/auth/", include("users.urls.auth")),
    path("api/users/", include("users.urls.users")),
    
    path('api/news/', include('news.urls')),
    path('api/stocks/', include('stocks.urls')),
    path("api/subscriptions/", include("subscriptions.urls")),
    path("api/readiness/", readiness),

]


if settings.DEBUG:
    import debug_toolbar
    urlpatterns = [
        path('__debug__/', include(debug_toolbar.urls)),
    ] + urlpatterns