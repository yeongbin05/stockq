from celery import shared_task
from django.conf import settings
from datetime import datetime, timedelta, timezone
import requests
import logging,time
import openai
from django.contrib.auth import get_user_model
from stocks.services import upsert_news_for_symbol
from celery import shared_task
from stocks.utils import allow_request
from stocks.services import store_daily_summaries_for_user
from stocks.models import Stock, News, DailyUserNews,Summary,FavoriteStock

logger = logging.getLogger(__name__)
User = get_user_model()


@shared_task(bind=True)
def fetch_favorite_news(self, days: int = 1):
    symbols = (
        FavoriteStock.objects
        .select_related("stock")
        .values_list("stock__symbol", flat=True)
        .distinct()
    )

    results = []
    for symbol in symbols:
        try:
            res = upsert_news_for_symbol(symbol, days=days)
            results.append({"symbol": symbol, **res})
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})
        finally:
            time.sleep(1)  # Finnhub 1초 1회면 유지

    # 요약 배치 호출 (아래 2번에서 stocks로 옮기는 걸 추천)
    from stocks.tasks import daily_news_summary_batch
    daily_news_summary_batch.delay()

    return results



@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def generate_summary_for_stock(self, symbol: str):
    """
    [NEW] 종목(Stock) 기준으로 뉴스 요약을 생성하여 Summary 모델에 저장
    (유저 수와 상관없이 종목당 1회만 실행되어 비용 절감)
    """
    # 1. API 키 확인
    if not settings.OPENAI_API_KEY:
        logger.error("[generate_summary] OPENAI_API_KEY not set")
        return {"error": "OpenAI API key not configured"}
    
    openai.api_key = settings.OPENAI_API_KEY

    today = datetime.now(timezone.utc).date()
    
    # 2. 뉴스 조회 (최근 1일, 해당 종목만)
    news_query = News.objects.filter(
        stocks__symbol__iexact=symbol,
        published_at__date=today
    ).order_by('-published_at')[:10]  # 토큰 제한 고려하여 최대 10개

    if not news_query.exists():
        logger.info(f"[generate_summary] {symbol}: 오늘 뉴스가 없어 요약 생략")
        return {"message": "No news found"}

    # 3. 프롬프트 데이터 준비
    news_texts = []
    for news in news_query:
        news_texts.append(f"제목: {news.headline}\n출처: {news.source}\n시간: {news.published_at.strftime('%Y-%m-%d')}\nURL: {news.url if news.url else 'N/A'}")
    
    combined_text = "\n\n".join(news_texts)

    # 4. 시스템 프롬프트 (금융 전문 애널리스트 페르소나)
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

    try:
        # 5. OpenAI API 호출
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=1500,
            temperature=0.3
        )
        
        summary_text = response.choices[0].message.content
        
        # 6. Summary 모델에 저장 (종목 + 날짜 기준)
        # 만약 Stock이 없으면 에러가 발생하므로 try-catch
        stock = Stock.objects.get(symbol__iexact=symbol)
        
        Summary.objects.update_or_create(
            stock=stock,
            date=today,
            defaults={"summary": summary_text}
        )
        logger.info(f"[generate_summary] {symbol} 요약 생성 및 저장 완료")
        return {"symbol": symbol, "status": "success"}

    except Stock.DoesNotExist:
        logger.error(f"[generate_summary] Stock {symbol} not found in DB")
        return {"error": f"Stock {symbol} not found"}
    except Exception as e:
        logger.error(f"[generate_summary] {symbol} 요약 실패: {e}")
        # 일시적 오류일 수 있으므로 재시도
        raise self.retry(countdown=60)

@shared_task
def daily_news_summary_batch():
    # 1. 유저가 아니라 '구독된 종목들'의 목록을 뽑습니다 (중복 제거)
    active_symbols = FavoriteStock.objects.values_list('stock__symbol', flat=True).distinct()
    
    # 2. 종목 개수만큼만 루프를 돕니다
    for symbol in active_symbols:
        # "이 종목 요약해줘" (딱 1번 실행)
        generate_summary_for_stock.delay(symbol)