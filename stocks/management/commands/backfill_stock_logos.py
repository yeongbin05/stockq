# stocks/management/commands/backfill_stock_logos.py

import requests

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from stocks.models import Stock


class Command(BaseCommand):
    help = "Backfill logo_url for favorited stocks using Finnhub profile2 API."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        limit = options["limit"]

        stocks = (
            Stock.objects
            .filter(favorited_by__isnull=False, logo_url="")
            .distinct()
            .order_by("symbol")[:limit]
        )

        updated = 0
        failed = 0

        for stock in stocks:
            try:
                response = requests.get(
                    "https://finnhub.io/api/v1/stock/profile2",
                    params={
                        "symbol": stock.symbol,
                        "token": settings.FINNHUB_API_KEY,
                    },
                    timeout=10,
                )
                response.raise_for_status()
                data = response.json()

                logo_url = data.get("logo") or ""
                web_url = data.get("weburl") or ""

                stock.logo_url = logo_url
                stock.web_url = web_url
                stock.profile_fetched_at = timezone.now()
                stock.save(
                    update_fields=[
                        "logo_url",
                        "web_url",
                        "profile_fetched_at",
                        "updated_at",
                    ]
                )

                updated += 1
                self.stdout.write(
                    self.style.SUCCESS(f"updated {stock.symbol}: {logo_url}")
                )

            except Exception as e:
                failed += 1
                self.stdout.write(
                    self.style.ERROR(f"failed {stock.symbol}: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(f"done updated={updated} failed={failed}")
        )