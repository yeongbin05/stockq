from .base import *
import os
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration

SENTRY_DSN = os.getenv("SENTRY_DSN")

if SENTRY_DSN:
    sentry_sdk.init(
        dsn=SENTRY_DSN,
        integrations=[DjangoIntegration()],
        environment=os.getenv("SENTRY_ENVIRONMENT", "production"),
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
    
DEBUG = False
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",")
SUMMARY_JOB_STUCK_SECONDS = 600
# 예: 배포에서 Postgres 사용
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        # environ은 없으면 에러 , getenv는없으면 None반환
        "NAME": os.environ["POSTGRES_DB"],
        "USER": os.environ["POSTGRES_USER"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

# 도커/배포용 Redis (docker-compose 서비스명이 'redis'라고 가정)
# DB 0: Celery broker, DB 1: Celery result backend, DB 2: Django cache, DB 3: rate limit bucket
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("DJANGO_CACHE_REDIS_URL", "redis://redis:6379/2"),
        "TIMEOUT": 60 * 10,
    }
}
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/1")

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")


# stockq/settings/prod.py 맨 아래에 추가

# 정적 파일들이 모일 경로 (Docker 컨테이너 내부 경로)
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# WhiteNoise가 정적 파일을 압축하고 캐싱하도록 설정 (성능 최적화)
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
}


# stockq/settings/prod.py 맨 아래

# ---------------------------------
# CORS & 보안 설정 (최종)
# ---------------------------------

CORS_ALLOW_ALL_ORIGINS = False 

# 허용할 프론트엔드 도메인 목록
CORS_ALLOWED_ORIGINS = [
    # 1. 실제 배포된 사이트 (가장 중요!)
    "https://stockqapp.com",
    "https://www.stockqapp.com",

    # 2. 로컬 개발용 (개발할 때만 필요, 나중에 주석 처리 가능)
    "http://localhost:3000",       # 웹 개발 기본 포트
    "http://127.0.0.1:3000",
    "http://localhost:8081",       # 앱 개발 기본 포트 (React Native)
]

# CSRF 신뢰 설정 (로그인 등 POST 요청 시 필수)
CSRF_TRUSTED_ORIGINS = [
    "https://stockqapp.com",
    "https://www.stockqapp.com",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]