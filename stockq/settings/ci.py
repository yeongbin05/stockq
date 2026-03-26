from .base import *
import os
DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "ci_db",
        "USER": "ci_user",
        "PASSWORD": "ci_password",
        "HOST": "db",   # docker-compose 서비스 이름
        "PORT": 5432,
    }
}

# 테스트 속도 최적화
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
]

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
FINNHUB_API_KEY = "dummy-ci-key"