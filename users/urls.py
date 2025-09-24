from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UserViewSet,LogoutView
from rest_framework_simplejwt.views import (
    TokenObtainPairView,
    TokenRefreshView,
)
from .views import KakaoLoginView 

router = DefaultRouter()
router.register("", UserViewSet, basename="user")

urlpatterns = [
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("auth/logout/", LogoutView.as_view(), name="logout"),
    path("social/kakao/", KakaoLoginView.as_view(), name="kakao_login"),
    path("", include(router.urls)),
]
