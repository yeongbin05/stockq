# stocks/services.py
import hashlib
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests
from django.conf import settings
from django.db import transaction

from .models import Stock, News, NewsStock


FINNHUB_COMPANY_NEWS = "https://finnhub.io/api/v1/company-news"
UTC = timezone.utc
DROP_QUERY_KEYS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid"
}

def canonicalize_url(url: str) -> str:
    """스킴/호스트 소문자, 프래그먼트 제거, 추적 파라미터 제거, 쿼리 정렬"""
    if not url:
        return ""
    p = urlparse(url)
    # 쿼리 정렬 + 추적 파라미터 제거
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
         if k.lower() not in DROP_QUERY_KEYS]
    q.sort()
    new_query = urlencode(q)
    # 프래그먼트 제거
    new_parts = (
        (p.scheme or "https").lower(),
        p.netloc.lower(),
        p.path or "/",
        p.params,
        new_query,
        ""  # fragment 제거
    )
    return urlunparse(new_parts)

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def _date_range(days: int):
    now = datetime.now(UTC)
    frm = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    to = now.strftime("%Y-%m-%d")
    return frm, to

def fetch_company_news(symbol: str, days: int = 1):
    if not settings.FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY not set")
    frm, to = _date_range(days)
    r = requests.get(
        FINNHUB_COMPANY_NEWS,
        params={"symbol": symbol, "from": frm, "to": to, "token": settings.FINNHUB_API_KEY},
        timeout=10,
    )
    r.raise_for_status()
    return r.json()  # list[dict]

@transaction.atomic
def upsert_news_for_symbol(symbol: str, days: int = 1) -> dict:
    """
    Finnhub에서 symbol 뉴스 가져와 stocks.News/NewsStock에 업서트.
    반환: {"created_news": X, "linked_pairs": Y, "skipped": Z}
    """
    data = fetch_company_news(symbol, days)
    created_news = 0
    linked_pairs = 0
    skipped = 0

    # 심볼에 해당하는 Stock이 있어야 링크 가능
    try:
        stock = Stock.objects.get(symbol=symbol)
    except Stock.DoesNotExist:
        # 필요시 자동 생성하려면 아래 주석 해제
        # stock = Stock.objects.create(symbol=symbol, name=symbol, exchange="", currency="USD")
        raise

    for item in data:
        raw_url = item.get("url") or ""
        if not raw_url:
            skipped += 1
            continue

        canonical = canonicalize_url(raw_url)
        url_hash = sha256(canonical)

        # published_at: finnhub "datetime"는 epoch(sec)
        ts = item.get("datetime")
        try:
            published_at = datetime.fromtimestamp(int(ts), tz=UTC) if ts else datetime.now(UTC)
        except Exception:
            published_at = datetime.now(UTC)

        defaults = {
            "headline": item.get("headline") or "",
            "url": raw_url,
            "canonical_url": canonical,
            "source": item.get("source") or (urlparse(canonical).netloc if canonical else None),
            "published_at": published_at,
            "language": item.get("lang", "en"),
            "raw_json": item,  # 원본 보관
        }

        news, created = News.objects.get_or_create(url_hash=url_hash, defaults=defaults)
        if created:
            created_news += 1
        else:
            # 필요 시 업데이트(헤드라인 변경 등)
            # news.headline = defaults["headline"] or news.headline
            # news.save(update_fields=["headline"])
            pass

        # through 테이블 링크
        ns_created = NewsStock.objects.get_or_create(news=news, stock=stock)[1]
        if ns_created:
            linked_pairs += 1

    return {"created_news": created_news, "linked_pairs": linked_pairs, "skipped": skipped}
    