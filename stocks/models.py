# stocks/models.py
from django.db import models
from django.conf import settings


class Stock(models.Model):
    symbol = models.CharField(max_length=10, unique=True)  # e.g., AAPL
    name = models.CharField(max_length=255)                               # e.g., Apple Inc.
    exchange = models.CharField(max_length=50)                            # e.g., NASDAQ, NYSE
    currency = models.CharField(max_length=10, blank=True, default="USD")
    type = models.CharField(max_length=20, blank=True, default="Stock")   # Stock, ETF...
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.symbol} - {self.name}"


class FavoriteStock(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favorites"
    )
    stock = models.ForeignKey(
        Stock, on_delete=models.CASCADE, related_name="favorited_by"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "stock"], name="uq_user_stock"),
        ]
        indexes = [
            models.Index(fields=["user", "stock"]),
        ]

    def __str__(self):
        return f"{self.user_id} - {self.stock.symbol}"

class News(models.Model):
    # 본문/링크
    headline = models.TextField()
    url = models.URLField(max_length=1000, blank=True, null=True)
    canonical_url = models.URLField(max_length=1000, blank=True, default="")
    # URL 정규화 기반 SHA-256 (중복 제거의 기준)
    url_hash = models.CharField(
    max_length=64,
    null=True,     # 최종
    blank=True,    # 최종
    unique=True     # 최종
)

    # 메타
    source = models.CharField(max_length=100, blank=True, null=True)      # 도메인/매체명
    published_at = models.DateTimeField(db_index=True)                    # UTC 권장
    language = models.CharField(max_length=8, blank=True, default="en")
    raw_json = models.JSONField(blank=True, null=True)

    # 관계
    stocks = models.ManyToManyField(
        Stock, through="NewsStock", related_name="news"
    )

    # 공통
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-published_at"]

    def __str__(self):
        return self.headline[:60]


class NewsStock(models.Model):
    news = models.ForeignKey(News, on_delete=models.CASCADE)
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("news", "stock")
        indexes = [models.Index(fields=["stock", "news"])]

    def __str__(self):
        return f"{self.stock.symbol} <-> {self.news.id}"


class Price(models.Model):
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name="prices")
    price = models.DecimalField(max_digits=12, decimal_places=2)
    change_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    timestamp = models.DateTimeField()  # UTC 권장
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("stock", "timestamp")    

    def __str__(self):
        return f"{self.stock.symbol} - {self.price} @ {self.timestamp}"


class Summary(models.Model):
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE, related_name="summaries")
    summary = models.TextField()
    recommendations = models.TextField(blank=True, null=True)  # 선택
    date = models.DateField()  # 요약 기준 날짜
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("stock", "date")
    

    def __str__(self):
        return f"{self.stock.symbol} - {self.date}"

# stocks/models.py (하단에 추가)

class DailyUserNews(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    date = models.DateField()
    stock = models.ForeignKey(Stock, on_delete=models.CASCADE)
    summary = models.TextField()  # GPT 요약 결과 등
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("user", "date", "stock")


    def __str__(self):
        return f"{self.user} - {self.stock.symbol} - {self.date}"
