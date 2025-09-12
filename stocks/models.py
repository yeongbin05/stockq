from django.db import models
from django.contrib.auth import get_user_model
User = get_user_model()

# models.py

class FavoriteStock(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='favorites', db_index=True)
    stock = models.ForeignKey("stocks.Stock", on_delete=models.CASCADE, related_name='favorited_by', db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "stock"], name="uniq_user_stock")
        ]
        indexes = [
            models.Index(fields=["user", "-created_at"]),
        ]

    def __str__(self):
        return f"{self.user_id}:{self.stock_id}"




# stocks/models.py

class Stock(models.Model):
    symbol = models.CharField(max_length=10, unique=True)  # 예: AAPL
    name = models.CharField(max_length=255)  # 예: Apple Inc.
    exchange = models.CharField(max_length=50)  # 예: US
    currency = models.CharField(max_length=10, null=True, blank=True)  # USD 등
    type = models.CharField(max_length=20, null=True, blank=True)  # Stock, ETF 등
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.symbol} - {self.name}"



class News(models.Model):
    stock = models.ForeignKey("stocks.Stock", on_delete=models.CASCADE, related_name='news')
    headline = models.TextField()
    url = models.URLField(max_length=500, blank=True, null=True)
    source = models.CharField(max_length=100, blank=True, null=True)
    published_at = models.DateTimeField()  # 뉴스 발행 시각
    raw_json = models.JSONField(blank=True, null=True)  # 원본 뉴스 데이터 저장용 (확장성)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.stock.symbol} - {self.headline[:30]}"


class Price(models.Model):
    stock = models.ForeignKey("stocks.Stock", on_delete=models.CASCADE, related_name='prices')
    price = models.DecimalField(max_digits=12, decimal_places=2)
    change_percent = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    timestamp = models.DateTimeField()  # 주가 기준 시점
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.stock.symbol} - {self.price}"


class Summary(models.Model):
    stock = models.ForeignKey("stocks.Stock", on_delete=models.CASCADE, related_name='summaries')
    summary = models.TextField()
    recommendations = models.TextField(blank=True, null=True)  # 대응 전략 (선택)
    date = models.DateField()  # 요약 기준 날짜
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.stock.symbol} - {self.date}"


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    stock = models.ForeignKey("stocks.Stock", on_delete=models.CASCADE, related_name='notifications')
    news = models.ForeignKey("stocks.News", on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=255)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.email} - {self.stock.symbol} - {'read' if self.is_read else 'unread'}"