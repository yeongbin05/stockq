import time
import requests
import hashlib
from datetime import datetime, timedelta, timezone as dt_timezone

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from django.db import IntegrityError, transaction

from stocks.models import News, FavoriteStock, Stock



def make_url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest() if url else None


def save_article(stock, art):
    """뉴스 기사 1건 안전하게 저장"""
    url_val = art.get("url")
    if not url_val:
        return False

    url_hash = make_url_hash(url_val)
    if not url_hash:
        return False

    # published_at 변환 (UTC → aware datetime)
    ts = art.get("datetime")
    if not ts:
        return False
    published_time = datetime.utcfromtimestamp(ts)
    published_time = timezone.make_aware(published_time, dt_timezone.utc)

    try:
        with transaction.atomic():
            obj, created = News.objects.get_or_create(
                url_hash=url_hash,
                defaults={
                    "headline": art.get("headline", "")[:500],
                    "url": url_val,
                    "canonical_url": url_val,
                    "source": art.get("source"),
                    "published_at": published_time,
                    "raw_json": art,
                },
            )
            if created:
                obj.stocks.add(stock)
            return created
    except IntegrityError:
        # 다른 워커가 동시에 같은 url_hash를 저장한 경우
        return False


def fetch_news_for_symbol(symbol: str, days: int = 1):
    """하나의 심볼에 대해 뉴스 수집 및 저장"""
    today = timezone.now().date()
    start_date = today - timedelta(days=days)

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from": start_date.isoformat(),
        "to": today.isoformat(),
        "token": settings.FINNHUB_API_KEY,
    }

    resp = requests.get(url, params=params, timeout=5)
    resp.raise_for_status()
    articles = resp.json()

    stock = Stock.objects.filter(symbol=symbol).first()
    if not stock:
        print(f"[fetch_news_for_symbol] {symbol} → Stock 없음, 저장 건너뜀")
        return {"symbol": symbol, "total": len(articles), "saved": 0}

    saved = 0
    for art in articles:
        if save_article(stock, art):
            saved += 1

    print(f"[fetch_news_for_symbol] {symbol} → {len(articles)}개 중 {saved}개 저장")
    return {"symbol": symbol, "total": len(articles), "saved": saved}


@shared_task(bind=True)
def fetch_favorite_news(self, days: int = 1):
    """즐겨찾기된 종목 뉴스 전부 직렬 수집 (분당 60개 제한 대응)"""
    symbols = (
        FavoriteStock.objects
        .select_related("stock")
        .values_list("stock__symbol", flat=True)
        .distinct()
    )

    results = []
    for symbol in symbols:
        try:
            res = fetch_news_for_symbol(symbol, days=days)
            results.append(res)
        except Exception as e:
            print(f"[fetch_favorite_news] {symbol} 처리 중 오류: {e}")
        finally:
            # API rate limit: 초당 1회
            time.sleep(1)

    print(f"[fetch_favorite_news] 완료 → {len(symbols)}개 심볼 처리")
    from stocks.tasks import daily_news_summary_batch
    daily_news_summary_batch.delay()
    return results
