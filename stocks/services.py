# stocks/services.py
import hashlib
from datetime import datetime, timedelta, timezone
from django.utils.timezone import now
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
import random
import time
import requests
from django.conf import settings
from django.db import transaction
import logging


logger = logging.getLogger(__name__)
from .models import Stock, News, NewsStock,DailyUserNews
from .rate_limit import get_finnhub_bucket
from .utils import normalize_url, make_url_hash

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


def _finnhub_backoff_seconds(attempt: int) -> float:
    return (2 ** attempt) + random.uniform(0, 0.5)


def _sleep_for_finnhub_429(attempt: int) -> float:
    sleep_seconds = _finnhub_backoff_seconds(attempt)
    time.sleep(sleep_seconds)
    return sleep_seconds


def fetch_company_news(symbol: str, days: int = 1, max_retries: int = 5):
    if not settings.FINNHUB_API_KEY:
        raise RuntimeError("FINNHUB_API_KEY not set")

    frm, to = _date_range(days)

    for attempt in range(max_retries):
        try:
            r = requests.get(
                FINNHUB_COMPANY_NEWS,
                params={
                    "symbol": symbol,
                    "from": frm,
                    "to": to,
                    "token": settings.FINNHUB_API_KEY,
                },
                timeout=10,
            )

            if r.status_code == 200:
                if attempt > 0:
                    logger.warning(
                        "[finnhub_retry_success] symbol=%s attempt=%s",
                        symbol,
                        attempt + 1,
                    )
                return r.json()

            if r.status_code == 429:
                if attempt == max_retries - 1:
                    raise Exception(f"Finnhub 429 Too Many Requests: {symbol}")

                sleep_seconds = _sleep_for_finnhub_429(attempt)
                logger.warning(
                    "[finnhub_429] symbol=%s attempt=%s sleep=%.2f",
                    symbol,
                    attempt + 1,
                    sleep_seconds,
                )
                continue

            logger.error(
                "[finnhub_http_error] symbol=%s status=%s body=%s",
                symbol,
                r.status_code,
                r.text[:200],
            )
            r.raise_for_status()

        except requests.RequestException as e:
            sleep_seconds = (2 ** attempt) + random.uniform(0, 0.5)
            logger.warning(
                "[finnhub_request_error] symbol=%s attempt=%s sleep=%.2f error=%s",
                symbol,
                attempt + 1,
                sleep_seconds,
                e,
            )

            if attempt == max_retries - 1:
                raise Exception(f"Finnhub request failed: {symbol}, error={e}")

            time.sleep(sleep_seconds)

    raise Exception(f"Finnhub fetch failed after retries: {symbol}")

from time import perf_counter
import logging

logger = logging.getLogger(__name__)

@transaction.atomic
def upsert_news_for_symbol(symbol: str, days: int = 1) -> dict:
    """
    Finnhub에서 symbol 뉴스 가져와 stocks.News/NewsStock에 업서트.
    반환: {"created_news": X, "linked_pairs": Y, "skipped": Z}
    """
    t_total_start = perf_counter()

    t_wait_start = perf_counter()
    if settings.FINNHUB_BUCKET_ENABLED:
        bucket = get_finnhub_bucket()
        if not bucket.wait_for_slot():
            t_wait_end = perf_counter()
            logger.info(
                "[upsert_news_for_symbol_breakdown] symbol=%s wait_slot=%.3fs fetch=0.000s stock_get=0.000s news_upsert=0.000s link_upsert=0.000s total=%.3fs status=rate_limited",
                symbol,
                t_wait_end - t_wait_start,
                t_wait_end - t_total_start,
            )
            raise Exception(f"Rate limit wait timeout: {symbol}")
    t_wait_end = perf_counter()

    t_fetch_start = perf_counter()
    data = fetch_company_news(symbol, days)
    t_fetch_end = perf_counter()

    created_news = 0
    linked_pairs = 0
    skipped = 0

    t_stock_start = perf_counter()
    try:
        stock = Stock.objects.get(symbol=symbol)
    except Stock.DoesNotExist:
        t_stock_end = perf_counter()
        logger.info(
            "[upsert_news_for_symbol_breakdown] symbol=%s wait_slot=%.3fs fetch=%.3fs stock_get=%.3fs news_upsert=0.000s link_upsert=0.000s total=%.3fs status=stock_not_found",
            symbol,
            t_wait_end - t_wait_start,
            t_fetch_end - t_fetch_start,
            t_stock_end - t_stock_start,
            t_stock_end - t_total_start,
        )
        raise
    t_stock_end = perf_counter()

    news_upsert_elapsed = 0.0
    link_upsert_elapsed = 0.0

    for item in data:
        raw_url = item.get("url") or ""
        if not raw_url:
            skipped += 1
            continue

        canonical = normalize_url(raw_url)
        url_hash = make_url_hash(raw_url)

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
            "raw_json": item,
        }

        t_news_upsert_start = perf_counter()
        news, created = News.objects.get_or_create(url_hash=url_hash, defaults=defaults)
        t_news_upsert_end = perf_counter()
        news_upsert_elapsed += (t_news_upsert_end - t_news_upsert_start)

        if created:
            created_news += 1

        t_link_upsert_start = perf_counter()
        ns_created = NewsStock.objects.get_or_create(news=news, stock=stock)[1]
        t_link_upsert_end = perf_counter()
        link_upsert_elapsed += (t_link_upsert_end - t_link_upsert_start)

        if ns_created:
            linked_pairs += 1

    t_total_end = perf_counter()

    logger.info(
        "[upsert_news_for_symbol_breakdown] symbol=%s wait_slot=%.3fs fetch=%.3fs stock_get=%.3fs news_upsert=%.3fs link_upsert=%.3fs total=%.3fs item_count=%s created_news=%s linked_pairs=%s skipped=%s",
        symbol,
        t_wait_end - t_wait_start,
        t_fetch_end - t_fetch_start,
        t_stock_end - t_stock_start,
        news_upsert_elapsed,
        link_upsert_elapsed,
        t_total_end - t_total_start,
        len(data),
        created_news,
        linked_pairs,
        skipped,
    )

    return {"created_news": created_news, "linked_pairs": linked_pairs, "skipped": skipped}


def store_daily_summaries_for_user(user, summaries_by_symbol: dict):
    """
    summaries_by_symbol = {
        "AAPL": "애플은 실적 발표 후 하락...",
        "MSFT": "마이크로소프트는 AI 투자 확대 중..."
    }
    """
    today = now().date()
    saved = 0

    for symbol, summary in summaries_by_symbol.items():
        try:
            stock = Stock.objects.get(symbol__iexact=symbol)
        except Stock.DoesNotExist:
            continue

        obj, created = DailyUserNews.objects.get_or_create(
            user=user,
            date=today,
            stock=stock,
            defaults={"summary": summary}
        )
        if created:
            saved += 1

    return saved
