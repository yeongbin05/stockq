# subscriptions/models.py
from django.db import models
from django.conf import settings
from django.utils import timezone

class Subscription(models.Model):
    class Plan(models.TextChoices):
        FREE = "FREE", "무료"
        PREMIUM = "PREMIUM", "유료"
        PRO = "PRO", "프로"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="subscriptions"
    )
    plan = models.CharField(
        max_length=20,
        choices=Plan.choices,
        default=Plan.FREE
    )
    start_date = models.DateField(auto_now_add=True)
    end_date = models.DateField(null=True, blank=True)
    active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan}"

    def is_active(self):
        if not self.active:
            return False
        if self.end_date and self.end_date < timezone.now().date():
            return False
        return True
