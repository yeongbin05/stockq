from celery import shared_task
from django.conf import settings
from datetime import datetime, timedelta, timezone
import requests
import logging

from stocks.utils import allow_request

logger = logging.getLogger(__name__)

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def fetch_news_for_symbol(self, symbol: str, days: int = 1):
    # --- rate-limit 체크 ---
    if not allow_request("rate_limit:finnhub", capacity=60, refill_rate=1):
        logger.warning(f"[rate-limit] {symbol} 요청 차단 (토큰 없음)")
        raise self.retry(countdown=1)

    # --- API 요청 ---
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days)

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from": start_date.isoformat(),
        "to": today.isoformat(),
        "token": settings.FINNHUB_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 429:
            logger.warning(f"[fetch_news_for_symbol] {symbol} → 429 Too Many Requests")
            raise self.retry(countdown=2)
        resp.raise_for_status()
        articles = resp.json()
    except Exception as e:
        logger.error(f"[fetch_news_for_symbol] {symbol} 요청 실패: {e}")
        raise

    logger.info(f"[fetch_news_for_symbol] {symbol} → {len(articles)}개 기사 가져옴")
    return {"symbol": symbol, "total": len(articles)}
