# stocks/utils.py
import hashlib, urllib.parse
import os
import redis,time


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


redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"),
    port=int(os.getenv("REDIS_PORT", 6379)),
    db=0,
)

# Lua 스크립트 로드
BASE_DIR = os.path.dirname(os.path.dirname(__file__))
LUA_PATH = os.path.join(BASE_DIR, "token_bucket.lua")

with open(LUA_PATH, "r", encoding="utf-8") as f:
    TOKEN_BUCKET_SCRIPT = f.read()

script_sha = redis_client.script_load(TOKEN_BUCKET_SCRIPT)



def allow_request(bucket: str, capacity: int, rate: int) -> bool:
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