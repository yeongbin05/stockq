# subscriptions/serializers.py
from rest_framework import serializers
from .models import Subscription

class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = ["id", "user", "plan", "start_date", "end_date", "active"]
        read_only_fields = ["id", "start_date"]
