# users/models.py
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("이메일은 필수입니다.")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        if extra_fields.get("is_staff") is not True:
            raise ValueError("superuser는 is_staff=True 이어야 합니다.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("superuser는 is_superuser=True 이어야 합니다.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractUser):
    username = None  # ✅ username 제거
    email = models.EmailField(unique=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.email


class SocialAccount(models.Model):
    PROVIDERS = (
        ("kakao", "Kakao"),
        ("google", "Google"),
        ("naver", "Naver"),
    )

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="social_accounts"
    )
    provider = models.CharField(max_length=20, choices=PROVIDERS)
    provider_user_id = models.CharField(max_length=255, unique=True)
    email = models.EmailField(blank=True, null=True)  # 소셜 계정 이메일 별도 보관
    extra_data = models.JSONField(default=dict)       # 원본 응답 전체 저장
    is_active = models.BooleanField(default=True)     # 연결 해제 시 soft delete

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # 마지막 갱신 시점