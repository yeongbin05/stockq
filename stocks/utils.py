import hashlib
import urllib.parse
import os
import time
import redis

def normalize_url(url: str) -> str:
    if not url:
        return ""
    u = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    q = [(k, v) for (k, v) in q if not k.lower().startswith("utm_")]
    query = urllib.parse.urlencode(sorted(q))
    netloc = u.netloc.lower()
    path = u.path or "/"
    return urllib.parse.urlunsplit((u.scheme, netloc, path, query, ""))

def make_url_hash(url: str) -> str:
    norm = normalize_url(url)
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
DJANGO_ENV = os.getenv("DJANGO_ENV", "dev")

redis_client = None
script_sha = None
TOKEN_BUCKET_SCRIPT = None
REDIS_AVAILABLE = False

try:
    redis_client = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()

    BASE_DIR = os.path.dirname(os.path.dirname(__file__))
    LUA_PATH = os.path.join(BASE_DIR, "token_bucket.lua")

    with open(LUA_PATH, "r", encoding="utf-8") as f:
        TOKEN_BUCKET_SCRIPT = f.read()

    script_sha = redis_client.script_load(TOKEN_BUCKET_SCRIPT)
    REDIS_AVAILABLE = True

except Exception as e:
    print(f"Redis 연결 실패: {e}")
    redis_client = None
    script_sha = None
    TOKEN_BUCKET_SCRIPT = None
    REDIS_AVAILABLE = False

def allow_request(bucket: str, capacity: int, rate: int) -> bool:
    global script_sha

    if not REDIS_AVAILABLE:
        if DJANGO_ENV == "dev":
            return True
        raise RuntimeError("Redis unavailable in non-dev environment")

    now_us = int(time.time() * 1_000_000)

    try:
        result = redis_client.evalsha(
            script_sha,
            1,
            bucket,
            str(capacity),
            str(rate),
            str(now_us),
        )
    except redis.exceptions.NoScriptError:
        script_sha = redis_client.script_load(TOKEN_BUCKET_SCRIPT)
        result = redis_client.evalsha(
            script_sha,
            1,
            bucket,
            str(capacity),
            str(rate),
            str(now_us),
        )

    return bool(result) and int(result[0]) == 1

def wait_for_slot(bucket: str, capacity: int, rate: int, timeout: float = 15.0, interval: float = 0.2) -> bool:
    start = time.time()

    while time.time() - start < timeout:
        if allow_request(bucket, capacity, rate):
            return True
        time.sleep(interval)

    return False

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