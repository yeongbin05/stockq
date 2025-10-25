from celery import shared_task
from django.conf import settings
from datetime import datetime, timedelta, timezone
import requests
import logging
import openai
from django.contrib.auth import get_user_model

from celery import shared_task
from stocks.utils import allow_request
from stocks.services import store_daily_summaries_for_user
from stocks.models import Stock, News, DailyUserNews

logger = logging.getLogger(__name__)
User = get_user_model()

@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=5)
def fetch_news_for_symbol(self, symbol: str, days: int = 1):
    # --- rate-limit 체크 ---
    if not allow_request("rate_limit:finnhub", capacity=60, refill_rate=1):
        logger.warning(f"[rate-limit] {symbol} 요청 차단 (토큰 없음)")
        raise self.retry(countdown=1)

    # --- API 요청 ---
    today = datetime.now(timezone.utc).date()
    start_date = today - timedelta(days=days)

    url = "https://finnhub.io/api/v1/company-news"
    params = {
        "symbol": symbol,
        "from": start_date.isoformat(),
        "to": today.isoformat(),
        "token": settings.FINNHUB_API_KEY,
    }

    try:
        resp = requests.get(url, params=params, timeout=5)
        if resp.status_code == 429:
            logger.warning(f"[fetch_news_for_symbol] {symbol} → 429 Too Many Requests")
            raise self.retry(countdown=2)
        resp.raise_for_status()
        articles = resp.json()
    except Exception as e:
        logger.error(f"[fetch_news_for_symbol] {symbol} 요청 실패: {e}")
        raise

    logger.info(f"[fetch_news_for_symbol] {symbol} → {len(articles)}개 기사 가져옴")
    return {"symbol": symbol, "total": len(articles)}



@shared_task
def add(x, y):
    return x + y


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_news_summary_with_openai(self, user_id: int, symbol: str = None):
    """
    OpenAI API를 사용하여 뉴스 요약 생성
    """
    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        logger.error(f"[generate_news_summary] User {user_id} not found")
        return {"error": "User not found"}
    
    if not settings.OPENAI_API_KEY:
        logger.error("[generate_news_summary] OPENAI_API_KEY not set")
        return {"error": "OpenAI API key not configured"}
    
    # OpenAI 클라이언트 설정
    openai.api_key = settings.OPENAI_API_KEY
    
    # 오늘 날짜
    today = datetime.now(timezone.utc).date()
    
    # 뉴스 조회 (최근 1일)
    news_query = News.objects.filter(
        stocks__symbol__iexact=symbol if symbol else None,
        published_at__date=today
    ).order_by('-published_at')[:10]  # 최근 10개 기사
    
    if not news_query.exists():
        logger.info(f"[generate_news_summary] No news found for {symbol or 'all stocks'} on {today}")
        return {"message": "No news found", "summaries": 0}
    
    # 뉴스 텍스트 준비 (구조화된 형태)
    news_texts = []
    for news in news_query:
        news_texts.append(f"제목: {news.headline}\n출처: {news.source}\n시간: {news.published_at.strftime('%Y-%m-%d')}\nURL: {news.url if news.url else 'N/A'}")
    
    combined_text = "\n\n".join(news_texts)
    
    try:
        # OpenAI API 호출 - 금융 전문 애널리스트 프롬프트 사용
        system_prompt = """너는 금융 전문 애널리스트 AI다. 아래 입력을 바탕으로 투자자가 빠르게 읽을 수 있는
'하루치 종목 리포트'를 한국어로 작성하라. 과장/권유/투자자문 표현은 금지한다.

[규칙]
1) 반드시 아래 '출력 형식' 섹션 구조를 그대로 사용한다(섹션 제목/이모지 고정).
2) 같은 이벤트를 다룬 중복/후속 기사는 합쳐서 한 줄로 통합 요약한다.
3) 사실만 기술하고, 수치/날짜/인용은 기사에 있는 것만 사용한다.
   - 숫자/퍼센트/날짜를 임의로 추정/창작하지 말고, 없으면 쓰지 말아라.
4) 루머·미확정 보도는 명시적으로 "루머/미확정"으로 표시하고 과도한 추론 금지.
5) 가격/수급·일정 입력이 없으면 "데이터 미제공" 또는 "없음"으로 표기한다.
6) '전체 분위기'는 긍정/중립/부정/혼합 중 하나로 선택하고, 한 문장 근거와
   0~100의 confidence를 함께 제시한다(출처 수/일치도/명확성 기반).
7) 마지막에 'JSON(머신리더블)' 블록을 반드시 첨부한다(키/스키마 고정).
8) 한국어로만 작성한다. 광고성·잡담·투자조언 문구는 금지한다.

[출력 형식]
📊 {DATE} {TICKER} ({COMPANY}) 뉴스 요약

✅ 1. 핵심 요약:
- (3~5줄) 오늘 기사들의 공통 핵심을 사실 위주로 통합 정리.
- 중복 이슈는 묶고, 새로운 전개가 있으면 "업데이트"로 표기.

💡 2. 투자 관점 주요 포인트:
- 긍정: (2~4개) 기술/실적/수요/경쟁력/파트너십 등
- 주의·리스크: (2~4개) 소송/규제/공급망/수요둔화/지연 등

📈 3. 가격/수급 스냅샷:
- 현재가/등락률/시가/전일종가 요약: 데이터 미제공
- 특이사항(거래량/프리·애프터마켓 등): 있으면 1줄, 없으면 생략

🗓️ 4. 다가오는 일정/촉매:
- 리스트 형식(예: 07-10 제품 이벤트, 07-25 실적발표). 없으면 "없음"

🌐 5. 섹터/거시 한 줄 요약:
- 데이터 미제공

🎯 6. 전체 분위기:
- 평가: [긍정/중립/부정/혼합], 근거 한 문장.
- confidence: NN/100

📝 7. 200자 내 요약:
- (공백 포함 200자 이내로 핵심 메시지 한 단락)

🔗 8. 출처(최대 5개, 중복 제거):
- 매체 | YYYY-MM-DD | 주요 키워드(최대 5단어) - (루머/미확정 시 표시)
- 각 항목 끝에 URL 1개 포함

---

JSON(머신리더블)  ※ 반드시 아래 스키마, 키 순서/영문키 유지
{
  "date": "{DATE}",
  "ticker": "{TICKER}",
  "company": "{COMPANY}",
  "sentiment": "positive|neutral|negative|mixed",
  "confidence": 0-100,
  "highlights": ["...", "..."],             // 핵심 요약 포인트 2~5개
  "bull_points": ["...", "..."],            // 긍정 요인
  "bear_points": ["...", "..."],            // 리스크 요인
  "price_snapshot": {
    "prev_close": number|null,
    "open": number|null,
    "current": number|null,
    "change_pct": number|null,
    "premarket": number|null,
    "after_hours": number|null,
    "volume_note": string|null
  },
  "upcoming": [ { "type":"earnings|product_event|regulatory|other", "date":"YYYY-MM-DD" } ],
  "sector_note": string|null,
  "summary_200": "공백 포함 200자 이내",
  "sources": [
    { "source":"Reuters", "url":"https://...", "published_at":"YYYY-MM-DD", "topic":"키워드", "status":"confirmed|rumor" }
  ],
  "novelty": "new|ongoing|update|rumor",    // 오늘 보도의 신선도/상태
  "risk_flags": ["lawsuit","regulation","supply_chain","delay"]  // 해당 시에만
}"""

        user_prompt = f"""📊 {today} {symbol} 뉴스 요약

[입력]
- 날짜: {today}
- 티커/회사: {symbol}
- 기사 목록:
{combined_text}

위 뉴스들을 바탕으로 금융 전문 애널리스트 리포트를 작성해주세요."""

        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {
                    "role": "system", 
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": user_prompt
                }
            ],
            max_tokens=1500,  # 더 긴 응답을 위해 토큰 수 증가
            temperature=0.3   # 더 일관된 분석을 위해 낮춤
        )
        
        summary = response.choices[0].message.content
        
        # 요약 저장
        if symbol:
            # 특정 종목 요약
            try:
                stock = Stock.objects.get(symbol__iexact=symbol)
                DailyUserNews.objects.update_or_create(
                    user=user,
                    date=today,
                    stock=stock,
                    defaults={"summary": summary}
                )
                logger.info(f"[generate_news_summary] Summary saved for {user.email} - {symbol}")
                return {"message": "Summary generated successfully", "symbol": symbol, "summary": summary}
            except Stock.DoesNotExist:
                logger.error(f"[generate_news_summary] Stock {symbol} not found")
                return {"error": f"Stock {symbol} not found"}
        else:
            # 사용자의 모든 관심종목에 대한 요약
            favorite_stocks = user.favorites.values_list('stock__symbol', flat=True)
            summaries_by_symbol = {}
            
            for stock_symbol in favorite_stocks:
                try:
                    stock = Stock.objects.get(symbol__iexact=stock_symbol)
                    # 각 종목별로 개별 요약 생성 (간단한 버전)
                    stock_news = news_query.filter(stocks__symbol__iexact=stock_symbol)
                    if stock_news.exists():
                        stock_texts = []
                        for news in stock_news[:5]:  # 각 종목당 최대 5개 기사
                            stock_texts.append(f"제목: {news.headline}\n출처: {news.source}\n시간: {news.published_at.strftime('%Y-%m-%d')}\nURL: {news.url if news.url else 'N/A'}")
                        stock_combined = "\n\n".join(stock_texts)
                        
                        stock_response = openai.chat.completions.create(
                            model="gpt-3.5-turbo",
                            messages=[
                                {
                                    "role": "system", 
                                    "content": system_prompt
                                },
                                {
                                    "role": "user",
                                    "content": f"""📊 {today} {stock_symbol} 뉴스 요약

[입력]
- 날짜: {today}
- 티커/회사: {stock_symbol}
- 기사 목록:
{stock_combined}

위 뉴스들을 바탕으로 금융 전문 애널리스트 리포트를 작성해주세요."""
                                }
                            ],
                            max_tokens=1500,
                            temperature=0.3
                        )
                        
                        summaries_by_symbol[stock_symbol] = stock_response.choices[0].message.content
                except Exception as e:
                    logger.error(f"[generate_news_summary] Error processing {stock_symbol}: {e}")
                    continue
            
            # 요약 저장
            saved_count = store_daily_summaries_for_user(user, summaries_by_symbol)
            logger.info(f"[generate_news_summary] {saved_count} summaries saved for {user.email}")
            return {"message": "Summaries generated successfully", "saved_count": saved_count, "summaries": summaries_by_symbol}
            
    except Exception as e:
        logger.error(f"[generate_news_summary] OpenAI API error: {e}")
        raise self.retry(countdown=60)  # 1분 후 재시도


@shared_task
def daily_news_summary_batch():
    """
    매일 실행되는 배치 작업: 모든 활성 사용자의 관심종목 뉴스 요약 생성
    """
    logger.info("[daily_news_summary_batch] Starting daily summary batch")
    
    # 활성 사용자들의 관심종목 수집
    users_with_favorites = User.objects.filter(
        favorites__isnull=False
    ).distinct()
    
    total_tasks = 0
    for user in users_with_favorites:
        # 각 사용자별로 요약 생성 태스크 큐에 추가
        generate_news_summary_with_openai.delay(user.id)
        total_tasks += 1
    
    logger.info(f"[daily_news_summary_batch] Queued {total_tasks} summary tasks")
    return {"message": f"Queued {total_tasks} summary tasks"}