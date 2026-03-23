from .base import *
DEBUG = True
ALLOWED_HOSTS = ["*"]
# INSTALLED_APPS += ["debug_toolbar"]

cors_idx = MIDDLEWARE.index("corsheaders.middleware.CorsMiddleware")

# 1) XBench를 cors 바로 아래로 이동
if "django_xbench.middleware.XBenchMiddleware" in MIDDLEWARE:
    MIDDLEWARE.remove("django_xbench.middleware.XBenchMiddleware")
MIDDLEWARE.insert(cors_idx + 1, "django_xbench.middleware.XBenchMiddleware")

# 2) DebugToolbar는 그 다음
# MIDDLEWARE.insert(cors_idx + 2, "debug_toolbar.middleware.DebugToolbarMiddleware")
# SQLite 또는 로컬 Postgres로 교체 가능
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

# 로컬 Redis
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": "redis://localhost:6379/1",
        "TIMEOUT": 600,
    }
}

REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = (
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
)

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")

import socket
hostname, _, ips = socket.gethostbyname_ex(socket.gethostname())
INTERNAL_IPS = [ip[:-1] + "1" for ip in ips] + ["127.0.0.1", "10.0.2.2"]

# local.py

# stockq/settings/local.py
import os

# 이 스위치가 있어야만 AllowAny가 허용됨
if os.getenv("DEBUG_MODE") == "True":
    REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = [
        "rest_framework.permissions.AllowAny",
    ]
else:
    # 서버에서는 무조건 인증 필요
    REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = [
        "rest_framework.permissions.IsAuthenticated",
    ]


CELERY_BROKER_URL = "redis://localhost:6379/0"
CELERY_RESULT_BACKEND = "redis://localhost:6379/0"