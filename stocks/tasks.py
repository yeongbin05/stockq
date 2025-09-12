from celery import shared_task
from django.core.management import call_command
from .models import Stock, Summary
from django.utils import timezone


@shared_task
def fetch_latest_news():
    # Reuse existing management command logic
    call_command('fetch_stock_data')


@shared_task
def generate_daily_summary(stock_id: int):
    # Placeholder summarization: mark that a summary was generated for today
    try:
        stock = Stock.objects.get(id=stock_id)
    except Stock.DoesNotExist:
        return
    today = timezone.now().date()
    Summary.objects.get_or_create(
        stock=stock,
        date=today,
        defaults={
            'summary': f'Auto summary for {stock.symbol} on {today}',
            'recommendations': 'N/A',
        }
    )

