# users/views/users.py
from django.contrib.auth import get_user_model
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from ..serializers import UserSerializer  # urls에서 users.views.users import 할 때 상대경로 안정적

User = get_user_model()


class IsAdminOrSelf(permissions.BasePermission):
    def has_object_permission(self, request, view, obj):
        return request.user.is_staff or obj == request.user


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def get_permissions(self):
        if self.action in ["create"]:  # 회원가입은 공개
            return [permissions.AllowAny()]
        if self.action in ["list", "destroy"]:  # 리스트/삭제는 관리자만
            return [permissions.IsAdminUser()]
        # retrieve/update/partial_update는 본인만(또는 관리자) 허용
        return [permissions.IsAuthenticated(), IsAdminOrSelf()]

    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)
