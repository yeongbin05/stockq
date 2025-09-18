from celery import shared_task
from django.utils import timezone

@shared_task
def test_celery_task():
    now = timezone.now()
    print(f"[Celery] 테스트 태스크 실행됨 at {now}")
    return str(now)
