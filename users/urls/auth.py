from django.urls import path
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from ..views.auth import LogoutView, DeactivateAccountView, KakaoLoginView

urlpatterns = [
    path("token/", TokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("deactivate/", DeactivateAccountView.as_view(), name="deactivate"),
    path("social/kakao/", KakaoLoginView.as_view(), name="kakao_login"),
]
