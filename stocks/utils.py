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



import spacy

nlp = spacy.load("en_core_web_sm")


def score_news_relevance(symbol: str, company_name: str, headline: str) -> tuple[int, bool, str]:
    text = (headline or "").strip()
    if not text:
        return 0, False, "empty_headline"

    text_lower = text.lower()
    symbol_lower = (symbol or "").lower().strip()
    company_lower = (company_name or "").lower().strip()

    # 회사명 alias
    company_alias = company_lower
    for suffix in [
        " inc.", " inc", " corporation", " corp.", " corp",
        " ltd.", " ltd", " co.", " co", " plc", " holdings",
    ]:
        company_alias = company_alias.replace(suffix, "")
    company_alias = company_alias.strip()

    score = 0
    reasons = []

    # 1) 직접 매칭
    if symbol_lower and symbol_lower in text_lower:
        score += 3
        reasons.append("symbol_match")

    if company_lower and company_lower in text_lower:
        score += 3
        reasons.append("company_match")

    if company_alias and company_alias != company_lower and company_alias in text_lower:
        score += 3
        reasons.append("company_alias_match")

    # 2) spaCy ORG 엔티티 추출
    doc = nlp(text)
    org_entities = [ent.text.lower() for ent in doc.ents if ent.label_ == "ORG"]

    if company_lower and any(company_lower in org or org in company_lower for org in org_entities):
        score += 2
        reasons.append("org_entity_match")

    if company_alias and any(company_alias in org or org in company_alias for org in org_entities):
        score += 2
        reasons.append("org_alias_match")

    # 3) 기업 이벤트 키워드
    finance_keywords = [
        "earnings", "revenue", "guidance", "forecast",
        "upgrade", "downgrade", "price target",
        "dividend", "acquisition", "merger", "partnership",
    ]
    matched_keywords = [kw for kw in finance_keywords if kw in text_lower]
    if matched_keywords:
        score += 1
        reasons.append(f"finance_keyword:{','.join(matched_keywords[:2])}")

    is_relevant = score >= 3
    reason = ", ".join(reasons) if reasons else "no_match"
    return score, is_relevant, reason