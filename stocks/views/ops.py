from django.http import HttpResponse

from stocks.models import Stock


def xbench_test(request):
    # 3만 개를 강제로 리스트로 만들어 메모리에 올림
    stocks = list(Stock.objects.all().values_list("id", flat=True))
    return HttpResponse(f"Loaded {len(stocks)} stocks for XBench test.")
