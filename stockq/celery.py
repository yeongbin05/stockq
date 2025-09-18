import os
from celery import Celery
from celery.schedules import crontab

# Django settings 모듈 지정
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "stockq.settings")

app = Celery("stockq")

# settings.py 안의 "CELERY_"로 시작하는 설정 불러오기
app.config_from_object("django.conf:settings", namespace="CELERY")

# 각 앱에 있는 tasks.py 자동 인식
app.autodiscover_tasks()

# beat 스케줄 등록
# beat 스케줄 등록
app.conf.beat_schedule = {
    "fetch-news-test-every-minute": {
        "task": "stocks.tasks.test_celery_task",
        "schedule": crontab(minute="*/1"),  # 매 1분마다 실행
        "args": (),
    },
}

