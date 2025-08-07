import asyncio
import aiohttp
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from stocks.models import Stock, News, Price
from decouple import config
from asgiref.sync import sync_to_async
from django.utils import timezone

API_KEY = config("FINNHUB_API_KEY")
BASE_URL = 'https://finnhub.io/api/v1'

@sync_to_async
def get_favorited_stocks():
    return list(Stock.objects.filter(favorited_by__isnull=False).distinct())

@sync_to_async
def save_news(stock, news):
    # 뉴스 시간 변환 + 타임존 aware 처리
    published_time = datetime.utcfromtimestamp(news.get("datetime"))
    published_time = timezone.make_aware(published_time, timezone.utc)

    News.objects.get_or_create(
        stock=stock,
        headline=news.get("headline", "")[:500],
        published_at=published_time,
        defaults={
            'url': news.get("url"),
            'source': news.get("source"),
            'raw_json': news
        }
    )

@sync_to_async
def save_price(stock, price_data):
    # 주가 시간도 timezone aware 처리
    timestamp = timezone.make_aware(datetime.utcnow(), timezone.utc)

    Price.objects.create(
        stock=stock,
        price=price_data.get("c", 0.0),
        change_percent=price_data.get("dp"),
        timestamp=timestamp
    )

async def fetch_data(session, stock):
    symbol = stock.symbol
    today = datetime.utcnow().date()
    from_date = today - timedelta(days=1)

    news_url = f"{BASE_URL}/company-news?symbol={symbol}&from={from_date}&to={today}&token={API_KEY}"
    price_url = f"{BASE_URL}/quote?symbol={symbol}&token={API_KEY}"

    try:
        async with session.get(news_url) as news_response, session.get(price_url) as price_response:
            news_data = await news_response.json()
            price_data = await price_response.json()

            for news in news_data:
                await save_news(stock, news)

            await save_price(stock, price_data)

            print(f"{symbol} 데이터 저장 완료.")

    except Exception as e:
        print(f"{symbol} 데이터 수집 오류:", e)

async def main():
    stocks = await get_favorited_stocks()
    batch_size = 30
    delay_per_batch = 60

    async with aiohttp.ClientSession() as session:
        for i in range(0, len(stocks), batch_size):
            batch = stocks[i:i+batch_size]
            tasks = [fetch_data(session, stock) for stock in batch]
            await asyncio.gather(*tasks)
            print(f"Batch {i//batch_size + 1} 완료. 대기 중...")
            if i + batch_size < len(stocks):
                await asyncio.sleep(delay_per_batch)

class Command(BaseCommand):
    help = "Fetch and store stock news and prices from Finnhub"

    def handle(self, *args, **kwargs):
        asyncio.run(main())
