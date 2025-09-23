import os
from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stockq.settings.local")

app = Celery("stockq")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

app.conf.timezone = "Asia/Seoul"  
app.conf.enable_utc = False       

app.conf.beat_schedule = {
    "fetch-news-favorites-kst6": {
        "task": "news.tasks.fetch_favorite_news",
        "schedule": crontab(hour=6, minute=0),  # 한국시간 6시 실행
    },
}
