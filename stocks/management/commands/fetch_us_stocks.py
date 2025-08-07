import requests
from django.core.management.base import BaseCommand
from stocks.models import Stock
from decouple import config

FINNHUB_API_KEY = config("FINNHUB_API_KEY")

class Command(BaseCommand):
    help = "Fetch and store NEW US stock symbols from Finnhub"

    def handle(self, *args, **kwargs):
        url = "https://finnhub.io/api/v1/stock/symbol"
        params = {"exchange": "US", "token": FINNHUB_API_KEY}
        self.stdout.write("📡 Fetching data from Finnhub...")

        response = requests.get(url, params=params)
        data = response.json()
        self.stdout.write(f"📦 Received {len(data)} stocks from Finnhub.")

        # 현재 DB에 존재하는 symbol 목록
        existing_symbols = set(Stock.objects.values_list('symbol', flat=True))

        stock_objs = []
        skipped = 0

        for item in data:
            symbol = item.get("displaySymbol")
            if not symbol:
                continue

            if symbol in existing_symbols:
                skipped += 1
                continue

            stock_objs.append(Stock(
                symbol=symbol,
                name=item.get("description", ""),
                exchange=item.get("mic", ""),
                currency=item.get("currency", ""),
                type=item.get("type", ""),
            ))

        Stock.objects.bulk_create(stock_objs, batch_size=1000)

        self.stdout.write(self.style.SUCCESS(
            f"✅ Saved {len(stock_objs)} new stocks. Skipped {skipped} already existing."
        ))
