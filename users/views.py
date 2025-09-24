# users/views.py
import requests
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import User,SocialAccount
from .serializers import UserSerializer
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import AllowAny
from django.db import transaction
from django.contrib.auth import get_user_model
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
        # retrieve/update/partial_update는 본인만 허용하도록 object permission에서 체크
        return [permissions.IsAuthenticated(), IsAdminOrSelf()]

    @action(detail=False, methods=["get"], permission_classes=[permissions.IsAuthenticated])
    def me(self, request):
        serializer = self.get_serializer(request.user)
        return Response(serializer.data)


class KakaoLoginView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        access_token = request.data.get("access_token")
        if not access_token:
            return Response({"detail": "access_token required"}, status=400)

        # 1) 카카오 사용자 조회
        resp = requests.get(
            "https://kapi.kakao.com/v2/user/me",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=5,
        )
        if resp.status_code != 200:
            return Response(
                {"detail": "kakao auth failed", "data": safe_json(resp)},
                status=400,
            )
        data = resp.json()

        kakao_id = str(data.get("id"))
        if not kakao_id:
            return Response({"detail": "kakao id missing"}, status=400)

        kakao_account = data.get("kakao_account", {}) or {}
        profile = kakao_account.get("profile", {}) or {}

        email = kakao_account.get("email")  # 없을 수 있음
        nickname = profile.get("nickname") or f"kakao_{kakao_id}"

        # 2) DB 매핑 (user_id NULL 문제 해결)
        with transaction.atomic():
            try:
                account = SocialAccount.objects.select_for_update().get(
                    provider="kakao",
                    provider_user_id=kakao_id,
                )
                user = account.user

                # 이메일/extra_data 업데이트
                changed = False
                if email and account.email != email:
                    account.email = email
                    changed = True
                if account.extra_data != data:
                    account.extra_data = data
                    changed = True
                if changed:
                    account.save(update_fields=["email", "extra_data", "updated_at"])

            except SocialAccount.DoesNotExist:
                # 새 유저 생성
                user = User.objects.create_user(
                    email=email or f"{nickname}+{kakao_id}@kakao.local",
                    password=None,  # 소셜 계정은 로컬 비밀번호 불필요
                )
                account = SocialAccount.objects.create(
                    user=user,
                    provider="kakao",
                    provider_user_id=kakao_id,
                    email=email,
                    extra_data=data,
                    is_active=True,
                )

            # 비활성 연결이면 거부
            if not account.is_active:
                return Response({"detail": "social account is inactive"}, status=403)

        # 3) JWT 발급
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {"id": user.id, "email": user.email},
                "provider": "kakao",
            },
            status=200,
        )



def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text, "status_code": resp.status_code}

class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()  # ✅ 블랙리스트 처리
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)
        
