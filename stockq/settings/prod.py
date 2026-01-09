from .base import *
DEBUG = False
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",") if os.getenv("ALLOWED_HOSTS") else ["*"]

# 예: 배포에서 Postgres 사용
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "stockq"),
        "USER": os.getenv("POSTGRES_USER", "stockq"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
        "HOST": os.getenv("POSTGRES_HOST", "db"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
    }
}

# 도커/배포용 Redis (docker-compose 서비스명이 'redis'라고 가정)
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.getenv("REDIS_URL", "redis://redis:6379/1"),
        "TIMEOUT": 60 * 10,
    }
}
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://redis:6379/2")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://redis:6379/3")

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