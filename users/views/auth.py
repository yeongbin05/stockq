# users/views/auth.py
import requests
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from ..models import SocialAccount

User = get_user_model()


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return {"text": resp.text, "status_code": resp.status_code}


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

        # 2) DB 매핑
        with transaction.atomic():
            try:
                account = SocialAccount.objects.select_for_update().get(
                    provider="kakao",
                    provider_user_id=kakao_id,
                )
                user = account.user

                # 이메일/extra_data/닉네임 업데이트
                changed = False
                if email and account.email != email:
                    account.email = email
                    changed = True
                if account.extra_data != data:
                    account.extra_data = data
                    changed = True
                if nickname and getattr(user, "nickname", None) != nickname:
                    user.nickname = nickname
                    user.save(update_fields=["nickname"])
                if changed:
                    account.save(update_fields=["email", "extra_data", "updated_at"])

            except SocialAccount.DoesNotExist:
                # 새 유저 생성
                user = User.objects.create_user(
                    email=email or f"{nickname}+{kakao_id}@kakao.local",
                    password=None,
                    nickname=nickname,
                )
                account = SocialAccount.objects.create(
                    user=user,
                    provider="kakao",
                    provider_user_id=kakao_id,
                    email=email,
                    extra_data=data,
                    is_active=True,
                )

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


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data["refresh"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception:
            return Response(status=status.HTTP_400_BAD_REQUEST)


class DeactivateAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def delete(self, request):
        user = request.user
        user.is_active = False
        user.save(update_fields=["is_active"])

        # username 없을 수 있어서 안전하게 처리
        identifier = getattr(user, "username", None) or getattr(user, "email", None) or str(user.id)

        return Response(
            {"detail": f"User {identifier} has been deactivated."},
            status=status.HTTP_200_OK,
        )
