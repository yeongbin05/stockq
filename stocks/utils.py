# stocks/utils.py
import hashlib, urllib.parse
import os, time
import redis

def normalize_url(url: str) -> str:
    if not url:
        return ""
    u = urllib.parse.urlsplit(url)
    # utm_* 제거
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    q = [(k, v) for (k, v) in q if not k.lower().startswith("utm_")]
    query = urllib.parse.urlencode(sorted(q))
    netloc = u.netloc.lower()
    path = u.path or "/"
    return urllib.parse.urlunsplit((u.scheme, netloc, path, query, ""))

def make_url_hash(url: str) -> str:
    norm = normalize_url(url)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

# ✅ 환경변수 REDIS_URL 기반으로 연결
REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

# 개발 환경에서 Redis 연결 실패 시 대체 처리
try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()  # 연결 테스트
    
    # Lua 스크립트 로드
    BASE_DIR = os.path.dirname(os.path.dirname(__file__))
    LUA_PATH = os.path.join(BASE_DIR, "token_bucket.lua")
    
    with open(LUA_PATH, "r", encoding="utf-8") as f:
        TOKEN_BUCKET_SCRIPT = f.read()
    
    script_sha = redis_client.script_load(TOKEN_BUCKET_SCRIPT)
    REDIS_AVAILABLE = True
except Exception as e:
    print(f"Redis 연결 실패: {e}")
    print("개발 모드: Redis 없이 실행")
    redis_client = None
    script_sha = None
    REDIS_AVAILABLE = False

def allow_request(bucket: str, capacity: int, rate: int) -> bool:
    if not REDIS_AVAILABLE:
        # Redis 없이 실행 시 항상 허용
        return True
    
    now_us = int(time.time() * 1_000_000)  # 현재 시각(µs)
    result = redis_client.evalsha(
        script_sha,
        1,               # KEYS 개수
        bucket,          # KEYS[1]
        str(capacity),   # ARGV[1]
        str(rate),       # ARGV[2]
        str(now_us),     # ARGV[3]
    )
    return result == 1
