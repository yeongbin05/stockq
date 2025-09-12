from rest_framework import viewsets, mixins, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from .models import FavoriteStock, Stock, News, Notification
from .serializers import FavoriteStockSerializer, StockSearchSerializer, NewsSerializer, NotificationSerializer, SummarySerializer
from django.db.models import Q
from rest_framework.exceptions import AuthenticationFailed
from .tasks import generate_daily_summary

# 즐겨찾기 ViewSet
class FavoriteStockViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin, mixins.DestroyModelMixin):
    serializer_class = FavoriteStockSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):

        auth_header = self.request.headers.get('Authorization', None)
        
        if not auth_header:
            raise AuthenticationFailed("Authorization header is missing")
        
        # 'Bearer ' 부분을 제거하고 실제 토큰만 추출
        token = auth_header.split(' ')[1] if len(auth_header.split(' ')) > 1 else None
        
        if not token:
            raise AuthenticationFailed("Token is missing")
        
        # 추출한 토큰을 출력 (디버깅용)
        print(f"Access Token: {token}")

        # 인증된 사용자만 접근 가능
        return FavoriteStock.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        user = self.request.user
        stock_id = self.request.data.get("stock_id")

        if not stock_id:
            raise serializers.ValidationError({"detail": "stock_id가 필요합니다."})

        try:
            stock = Stock.objects.get(id=stock_id)
        except Stock.DoesNotExist:
            raise serializers.ValidationError({"detail": "해당 종목이 존재하지 않습니다."})

        # 중복 확인
        if FavoriteStock.objects.filter(user=user, stock=stock).exists():  # ✅ 정답
            raise serializers.ValidationError({"detail": "이미 즐겨찾기에 등록된 종목입니다."})

        serializer.save(user=user, stock=stock)

    # 표준 REST: 삭제는 DELETE /favorites/<id>/ 사용



# 종목 검색 ViewSet
class StockViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Stock.objects.all().order_by('symbol')
    serializer_class = StockSearchSerializer
    permission_classes = [IsAuthenticated]

    def list(self, request, *args, **kwargs):
        q = request.query_params.get('q', '').strip()
        if not q:
            queryset = self.get_queryset()[:100]
            serializer = self.get_serializer(queryset, many=True, context={'request': request})
            return Response(serializer.data)

        exact = Stock.objects.filter(symbol__iexact=q)
        partial = Stock.objects.filter(Q(symbol__icontains=q) | Q(name__icontains=q))
        if exact.exists():
            queryset = list(exact) + [s for s in partial if s not in exact]
        else:
            queryset = list(partial)
        serializer = self.get_serializer(queryset, many=True, context={'request': request})
        return Response(serializer.data)


class NewsViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    queryset = News.objects.select_related('stock').order_by('-published_at')
    serializer_class = NewsSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        symbol = self.request.query_params.get('symbol')
        favorites = self.request.query_params.get('favorites')
        if symbol:
            qs = qs.filter(stock__symbol__iexact=symbol)
        if favorites in ["1", "true", "True"]:
            fav_ids = FavoriteStock.objects.filter(user=self.request.user).values_list('stock_id', flat=True)
            qs = qs.filter(stock_id__in=list(fav_ids))
        return qs


class NotificationViewSet(mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"], url_path="mark-read")
    def mark_read(self, request, pk=None):
        try:
            notif = Notification.objects.get(id=pk, user=request.user)
        except Notification.DoesNotExist:
            return Response({"detail": "Not found"}, status=404)
        notif.is_read = True
        notif.save(update_fields=["is_read"])
        return Response({"status": "ok"})


class SummaryViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        stock_id = request.data.get('stock_id')
        if not stock_id:
            raise serializers.ValidationError({"detail": "stock_id is required"})
        generate_daily_summary.delay(int(stock_id))
        return Response({"status": "queued"})
