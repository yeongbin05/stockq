from django.urls import path, include
from rest_framework.routers import DefaultRouter
from ..views.users import UserViewSet

router = DefaultRouter()
router.register("", UserViewSet, basename="user")

urlpatterns = [
    path("", include(router.urls)),
]
