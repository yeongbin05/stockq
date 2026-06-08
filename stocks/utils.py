import hashlib
import urllib.parse
import os
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


def allow_request(
    bucket: str,
    capacity: int,
    rate: float | None = None,
    refill_rate: float | None = None,
) -> bool:
    """Compatibility wrapper around the shared RedisTokenBucket implementation."""
    from stocks.rate_limit import RedisTokenBucket

    effective_rate = rate if rate is not None else refill_rate
    if effective_rate is None:
        raise ValueError("rate or refill_rate must be provided")

    limiter = RedisTokenBucket(
        key=bucket,
        capacity=capacity,
        refill_rate_per_sec=effective_rate,
        redis_url=REDIS_URL,
    )

    try:
        return limiter.consume(tokens=1).allowed
    except redis.RedisError:
        if DJANGO_ENV == "dev":
            return True
        raise RuntimeError("Redis unavailable in non-dev environment")


def wait_for_slot(
    bucket: str,
    capacity: int,
    rate: float,
    timeout: float = 15.0,
    interval: float = 0.2,
) -> bool:
    """Compatibility wrapper for callers that still pass bucket settings directly."""
    from stocks.rate_limit import RedisTokenBucket

    limiter = RedisTokenBucket(
        key=bucket,
        capacity=capacity,
        refill_rate_per_sec=rate,
        redis_url=REDIS_URL,
    )

    try:
        return limiter.wait_for_slot(timeout=timeout, interval=interval)
    except redis.RedisError:
        if DJANGO_ENV == "dev":
            return True
        raise RuntimeError("Redis unavailable in non-dev environment")

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