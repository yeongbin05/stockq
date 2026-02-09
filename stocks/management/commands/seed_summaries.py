from __future__ import annotations

import random
import string
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from stocks.models import Stock, Summary


def random_sentence(min_words=8, max_words=18):
    n = random.randint(min_words, max_words)
    words = []
    for _ in range(n):
        wlen = random.randint(3, 10)
        words.append("".join(random.choices(string.ascii_lowercase, k=wlen)))
    return " ".join(words).capitalize() + "."


def fake_summary_text():
    return "\n".join(random_sentence() for _ in range(3))


class Command(BaseCommand):
    help = "Seed Summary dummy data (Stock × Date). No external API calls."

    def add_arguments(self, parser):
        parser.add_argument("--symbols", type=int, default=100)
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument(
            "--mode",
            choices=["upsert", "append"],
            default="upsert",
        )
        parser.add_argument("--dry-run", action="store_true")

    @transaction.atomic
    def handle(self, *args, **options):
        symbols_n = options["symbols"]
        days = options["days"]
        mode = options["mode"]
        dry_run = options["dry_run"]

        # 1. Stock 가져오기
        stocks = list(Stock.objects.order_by("id")[:symbols_n])
        if not stocks:
            self.stderr.write(self.style.ERROR("Stock 테이블이 비어 있습니다."))
            return

        if len(stocks) < symbols_n:
            self.stdout.write(
                self.style.WARNING(f"Stock {len(stocks)}개만 사용합니다.")
            )

        today = timezone.now().date()
        dates = [today - timedelta(days=i) for i in range(days)]
        dates.sort()

        # 2. 기존 데이터 확인 (upsert 모드)
        existing = set()
        if mode == "upsert":
            existing = set(
                Summary.objects.filter(
                    stock__in=stocks,
                    date__in=dates,
                ).values_list("stock_id", "date")
            )

        to_create = []

        for stock in stocks:
            for d in dates:
                key = (stock.id, d)
                if mode == "upsert" and key in existing:
                    continue

                to_create.append(
                    Summary(
                        stock=stock,
                        date=d,
                        summary=fake_summary_text(),
                        recommendations=random_sentence(6, 12),
                    )
                )

        self.stdout.write(
            f"Stocks={len(stocks)} Days={days} → Insert {len(to_create)} rows"
        )

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN: DB write skipped"))
            return

        if not to_create:
            self.stdout.write(self.style.SUCCESS("이미 모든 데이터가 존재합니다."))
            return

        Summary.objects.bulk_create(to_create, batch_size=1000)
        self.stdout.write(self.style.SUCCESS("Summary 시드 데이터 생성 완료"))
