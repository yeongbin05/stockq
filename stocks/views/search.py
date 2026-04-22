from django.db.models import Q
from rest_framework import viewsets
from rest_framework.pagination import CursorPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from stocks.models import Stock
from stocks.serializers import StockSearchSerializer


class StockCursorPagination(CursorPagination):
    page_size = 20
    # Cursor 방식의 핵심: 정렬 기준이 명확해야 다음 이정표(Cursor)를 찾을 수 있습니다.
    ordering = "id"


class StockSearchViewSet(viewsets.ViewSet):
    """
    미국 주식 종목 검색 API
    - 페이지네이션을 적용하여 수만 개의 데이터를 조각내어 가져옵니다.
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StockCursorPagination

    def list(self, request):
        q = request.query_params.get("q", "").strip()

        if not q:
            return Response([])

        queryset = Stock.objects.filter(Q(symbol__icontains=q) | Q(name__icontains=q)).order_by(
            "id"
        )

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)

        if page is not None:
            serializer = StockSearchSerializer(page, many=True, context={"request": request})
            return paginator.get_paginated_response(serializer.data)

        serializer = StockSearchSerializer(queryset, many=True, context={"request": request})
        return Response(serializer.data)
