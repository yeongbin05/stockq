import requests
from django.http import JsonResponse
from django.views import View
from datetime import datetime, timedelta
from django.conf import settings

class NewsSummaryView(View):
    def get(self, request):
        symbol = request.GET.get('symbol', 'AAPL')  # 기본값 AAPL

        # 오늘 날짜와 하루 전 날짜 (UTC 기준)
        to_date = datetime.utcnow().date()
        from_date = to_date - timedelta(days=1)

        url = (
            f"https://finnhub.io/api/v1/company-news"
            f"?symbol={symbol}&from={from_date}&to={to_date}&token={settings.FINNHUB_API_KEY}"
        )

        response = requests.get(url)
        if response.status_code != 200:
            return JsonResponse({'error': 'Failed to fetch news'}, status=500)

        raw_data = response.json()
        yahoo_news = [
            {
                "headline": item["headline"],
                "summary": item["summary"],
                "url": item["url"]
            }
            for item in raw_data
            if item.get("source", "").lower() == "yahoo"
        ]

        # 터미널 출력
        print(f"\n[Yahoo 뉴스 요약 - {symbol}]")
        for idx, article in enumerate(yahoo_news, 1):
            print(f"\n📌 뉴스 {idx}")
            print(f"제목: {article['headline']}")
            print(f"요약: {article['summary']}")
            print(f"링크: {article['url']}")

        return JsonResponse(yahoo_news, safe=False)
