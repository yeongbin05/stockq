from rest_framework import viewsets, mixins, serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.decorators import action
from rest_framework.filters import SearchFilter
from .models import FavoriteStock, Stock
from .serializers import FavoriteStockSerializer, StockSearchSerializer
from django.db.models import Q
from rest_framework.exceptions import AuthenticationFailed

from rest_framework.decorators import permission_classes

# 즐겨찾기 ViewSet
class FavoriteStockViewSet(viewsets.GenericViewSet, mixins.ListModelMixin, mixins.CreateModelMixin):
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

    @action(detail=True, methods=["delete"])
    def remove(self, request, pk=None):
        instance = self.get_queryset().filter(stock__symbol=pk).first()
        if instance:
            instance.delete()
            return Response({"message": f"{pk} 즐겨찾기 삭제됨"})
        return Response({"message": f"{pk}은 즐겨찾기 목록에 없음"}, status=404)



# 종목 검색 ViewSet
class StockSearchViewSet(viewsets.ViewSet):
    def list(self, request):
        q = request.query_params.get('q', '').strip()
        if not q:
            return Response([])

        # 1. symbol 정확 일치
        exact = Stock.objects.filter(symbol__iexact=q)

        # 2. symbol, name 부분일치 (icicontains)
        partial = Stock.objects.filter(
            Q(symbol__icontains=q) | Q(name__icontains=q)
        )

        # 3. 정확 일치 + 부분일치(중복 제거, 정확 일치가 맨 앞에 오도록)
        if exact.exists():
            queryset = list(exact) + [s for s in partial if s not in exact]
        else:
            queryset = list(partial)

        serializer = StockSearchSerializer(queryset, many=True,context={'request': request})
        return Response(serializer.data)
