from django.core.management.base import BaseCommand
import time
from django.db import connection, reset_queries
from django.contrib.auth import get_user_model
from django.db.models import Exists, OuterRef
from stocks.models import Stock, FavoriteStock
from stocks.serializers import StockSearchSerializer
from rest_framework.test import APIRequestFactory

User = get_user_model()

class Command(BaseCommand):
    help = 'N+1 문제 발생 여부를 테스트합니다.'

    def handle(self, *args, **options):
        # 데이터 개수 설정 (블로그용으로 1000개 추천)
        DATA_COUNT = 1000 
        
        self.stdout.write(f"--- 1. 데이터 {DATA_COUNT}개 준비 중... ---")
        user, _ = User.objects.get_or_create(email="blog_test@test.com", defaults={"password": "password123"})
        
        # 주식 데이터 대량 생성
        stocks = []
        for i in range(DATA_COUNT):
            s, _ = Stock.objects.get_or_create(
                symbol=f"BLOG{i}", 
                defaults={"name": f"Blog Stock {i}", "exchange": "NASDAQ"}
            )
            stocks.append(s)
        
        # 앞쪽 50개만 즐겨찾기
        FavoriteStock.objects.filter(user=user, stock__symbol__startswith="BLOG").delete()
        for s in stocks[:50]:
            FavoriteStock.objects.get_or_create(user=user, stock=s)
        
        self.stdout.write(f"   -> 세팅 완료\n")

        # 요청 준비
        factory = APIRequestFactory()
        request = factory.get('/')
        request.user = user 

        self.stdout.write(f"--- 2. 성능 측정 시작 ({DATA_COUNT}개 조회) ---")
        reset_queries()
        
        start_time = time.time()
        
        # ---------------------------------------------------------
        # [주의] Before 스크린샷 찍을 땐 아래 '개선 전' 코드를 주석 해제하세요!
        # ---------------------------------------------------------
        
        # 1) 개선 전 (Serializer에서 N+1 발생) 상황 재현용 쿼리
        # queryset = Stock.objects.filter(symbol__startswith="BLOG").order_by("id")
        
        # 2) 개선 후 (Annotate 최적화) 쿼리
        queryset = Stock.objects.filter(symbol__startswith="BLOG").order_by("id")
        is_fav_subquery = FavoriteStock.objects.filter(user=user, stock=OuterRef('pk'))
        queryset = queryset.annotate(is_favorite_annotated=Exists(is_fav_subquery))
        
        # Serializer 실행
        serializer = StockSearchSerializer(queryset, many=True, context={"request": request})
        data = serializer.data
        
        end_time = time.time()
        
        query_count = len(connection.queries)
        duration = end_time - start_time
        
        self.stdout.write(f"\n📊 [결과 리포트]")
        self.stdout.write(f"   - 조회 데이터 : {len(data)}개")
        self.stdout.write(f"   - 실행 쿼리 수: {query_count}개")
        self.stdout.write(f"   - 총 소요 시간: {duration:.4f}초")
        
        if query_count > 100:
             self.stdout.write(self.style.ERROR(f"\n🚨 [Before] N+1 발생! (쿼리 폭발)"))
        else:
             self.stdout.write(self.style.SUCCESS(f"\n✅ [After] 최적화 완료! (쿼리 {query_count}개)"))