from celery import shared_task
from django.conf import settings
from datetime import datetime, timedelta,time, timezone as dt_timezone
from django.utils import timezone
from zoneinfo import ZoneInfo
import openai,json,logging
from django.contrib.auth import get_user_model
from stocks.services import upsert_news_for_symbol  
from stocks.models import Stock, News,Summary,FavoriteStock,SummaryGenerationLog
from time import perf_counter
from stocks.utils import score_news_relevance
logger = logging.getLogger(__name__)
User = get_user_model()

def _get_utc_range_from_kst_date(target_date=None):
    kst = ZoneInfo("Asia/Seoul")

    if target_date is None:
        target_date = timezone.now().astimezone(kst).date()

    start_kst = datetime.combine(target_date, time.min, tzinfo=kst)
    end_kst = start_kst + timedelta(days=1)

    start_utc = start_kst.astimezone(ZoneInfo("UTC"))
    end_utc = end_kst.astimezone(ZoneInfo("UTC"))

    return target_date, start_utc, end_utc

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
            pass  # Finnhub 1초 1회면 유지

    # 요약 배치 호출 (아래 2번에서 stocks로 옮기는 걸 추천)
    from stocks.tasks import daily_news_summary_batch
    daily_news_summary_batch.delay()

    return results

def estimate_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)

def _generate_summary_for_stock(symbol: str, target_date=None):
    """
    실제 요약 생성 로직
    Celery task와 측정 함수에서 공용으로 사용
    """
    if not settings.OPENAI_API_KEY:
        logger.error("[generate_summary] OPENAI_API_KEY not set")
        return {"error": "OpenAI API key not configured"}

    openai.api_key = settings.OPENAI_API_KEY
   

    target_date, start_utc, end_utc = _get_utc_range_from_kst_date(target_date)
    stock = Stock.objects.get(symbol__iexact=symbol)

    news_query = News.objects.filter(
        stocks__symbol__iexact=symbol,
        published_at__gte=start_utc,
        published_at__lt=end_utc,
    ).order_by("-published_at")[:10]

    raw_count = news_query.count()
    logger.info(f"[generate_summary] symbol={symbol} raw_count={raw_count}")

    if raw_count == 0:
        SummaryGenerationLog.objects.create(
            stock=stock,
            date=target_date,
            before_input_tokens=0,
            after_input_tokens=0,
            raw_count=0,
            relevant_count=0,
            status="no_news",
            elapsed_ms=0,
        )
        logger.info(f"[generate_summary] {symbol}: 오늘 뉴스가 없어 요약 생략")
        return {"message": "No news found"}

    scored_news = []
    for news in news_query:
        score, is_relevant, reason = score_news_relevance(
            symbol=symbol,
            company_name=stock.name,
            headline=news.headline,
        )
        scored_news.append({
            "news": news,
            "relevance_score": score,
            "is_relevant": is_relevant,
            "reason": reason,
        })

    relevant_news = [item for item in scored_news if item["is_relevant"]]
    relevant_count = len(relevant_news)

    logger.info(
        f"[generate_summary] symbol={symbol} raw_count={raw_count} relevant_count={relevant_count}"
    )

    

    kst = timezone.get_fixed_timezone(9 * 60)

    all_news_texts = []
    for news in news_query:
        published_kst = news.published_at.astimezone(kst).strftime("%Y-%m-%d %H:%M")
        all_news_texts.append(
            f"제목: {news.headline}\n"
            f"출처: {news.source}\n"
            f"시간: {published_kst}\n"
            f"URL: {news.url if news.url else 'N/A'}"
        )

    news_texts = []
    for item in relevant_news:
        news = item["news"]
        published_kst = news.published_at.astimezone(kst).strftime("%Y-%m-%d %H:%M")
        news_texts.append(
            f"제목: {news.headline}\n"
            f"출처: {news.source}\n"
            f"시간: {published_kst}\n"
            f"URL: {news.url if news.url else 'N/A'}"
        )

    before_combined_text = "\n\n".join(all_news_texts)
    combined_text = "\n\n".join(news_texts)

    system_prompt = """너는 금융 전문 애널리스트 AI다.
        입력된 뉴스들을 바탕으로 한국어 요약 결과를 반드시 JSON 객체 하나로만 반환하라.
        마크다운, 설명문, 코드블록, ```json 같은 표시는 절대 출력하지 마라.

        [규칙]
        1) 반드시 유효한 JSON 객체 하나만 반환한다.
        2) 키는 아래 형식을 정확히 따른다:
        - ticker: 문자열
        - date: 문자열 (YYYY-MM-DD)
        - news_summary: 문자열 배열
        - price_and_volume: 문자열
        - overall_sentiment: 객체
            - sentiment: "긍정" | "중립" | "부정" | "혼합"
            - rationale: 문자열
            - confidence: 0~100 정수
        3) 같은 이벤트를 다룬 중복/후속 기사는 하나로 합친다.
        4) 기사에 없는 내용은 추측하지 않는다.
        5) 가격/수급 정보가 없으면 "데이터 미제공"으로 넣는다.
        6) 한국어로만 작성한다.
        """
    
    before_user_prompt = f"""📊 {target_date} {symbol} 뉴스 요약

    [입력]
    - 날짜: {target_date}
    - 티커/회사: {symbol}
    - 기사 목록:
    {before_combined_text}

    위 뉴스들을 바탕으로 조건에 맞는 JSON 객체만 반환해주세요."""


    user_prompt = f"""📊 {target_date} {symbol} 뉴스 요약

    [입력]
    - 날짜: {target_date}
    - 티커/회사: {symbol}
    - 기사 목록:
    {combined_text}

    위 뉴스들을 바탕으로 조건에 맞는 JSON 객체만 반환해주세요."""
    
    before_input_tokens = estimate_token_count(system_prompt + "\n" + before_user_prompt)
    after_input_tokens = estimate_token_count(system_prompt + "\n" + user_prompt)

    if relevant_count == 0:
        SummaryGenerationLog.objects.create(
            stock=stock,
            date=target_date,
            before_input_tokens=before_input_tokens,
            after_input_tokens=after_input_tokens,
            raw_count=raw_count,
            relevant_count=0,
            status="no_relevant_news",
            elapsed_ms=0,
        )
        logger.info(f"[generate_summary] {symbol}: 관련 뉴스가 없어 요약 생략")
        return {"message": "No relevant news found"}
    
    t0 = perf_counter()
    try:
        response = openai.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=1200,
            temperature=0.2
        )
    except Exception as e:
        logger.error(f"OpenAI call failed: {e}")
        SummaryGenerationLog.objects.create(
            stock=stock,
            date=target_date,
            before_input_tokens=before_input_tokens,
            after_input_tokens=after_input_tokens,
            raw_count=raw_count,
            relevant_count=relevant_count,
            status="failed",
            elapsed_ms=int((perf_counter() - t0) * 1000),
            error_message=str(e),
        )
        return {"error": "openai_call_failed"}

    summary_text = response.choices[0].message.content
    try:
        summary_json = json.loads(summary_text)
    except Exception as e:
        logger.error(f"JSON parse failed: {e}")
        SummaryGenerationLog.objects.create(
            stock=stock,
            date=target_date,
            before_input_tokens=before_input_tokens,
            after_input_tokens=after_input_tokens,
            raw_count=raw_count,
            relevant_count=relevant_count,
            status="failed",
            elapsed_ms=int((perf_counter() - t0) * 1000),
            error_message=f"json_parse_failed: {str(e)}",
        )
        return {"error": "json_parse_failed"}

    Summary.objects.update_or_create(
        stock=stock,
        date=target_date,
        defaults={
            "summary": summary_json
        }
    )
    t1 = perf_counter()
    SummaryGenerationLog.objects.create(
        stock=stock,
        date=target_date,
        before_input_tokens=before_input_tokens,
        after_input_tokens=after_input_tokens,
        raw_count=raw_count,
        relevant_count=relevant_count,
        status="success",
        elapsed_ms=int((t1 - t0) * 1000),
    )

    logger.info(
        f"[generate_summary] symbol={symbol} llm_and_save_elapsed={t1 - t0:.2f}s"
    )

    return {
        "symbol": symbol,
        "status": "success",
        "elapsed": round(t1 - t0, 2),
        "raw_count": raw_count,
        "relevant_count": relevant_count,
    }
@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)

def generate_summary_for_stock(self, symbol: str):
    try:
        return _generate_summary_for_stock(symbol)
    except Stock.DoesNotExist:
        logger.error(f"[generate_summary] Stock {symbol} not found in DB")
        return {"error": f"Stock {symbol} not found"}
    except Exception as e:
        logger.error(f"[generate_summary] {symbol} 요약 실패: {e}")
        raise self.retry(countdown=60)
    
@shared_task
def daily_news_summary_batch():
    # 1. 유저가 아니라 '구독된 종목들'의 목록을 뽑습니다 (중복 제거)
    active_symbols = FavoriteStock.objects.values_list('stock__symbol', flat=True).distinct()
    
    # 2. 종목 개수만큼만 루프를 돕니다
    for symbol in active_symbols:
        # "이 종목 요약해줘" (딱 1번 실행)
        generate_summary_for_stock.delay(symbol)



def measure_pipeline_for_symbol(symbol: str, days: int = 1):
    t0 = perf_counter()

    fetch_result = upsert_news_for_symbol(symbol, days=days)
    t1 = perf_counter()

    kst = ZoneInfo("Asia/Seoul")
    target_date = timezone.now().astimezone(kst).date() - timedelta(days=days - 1)

    summary_result = _generate_summary_for_stock(symbol, target_date=target_date)
    t2 = perf_counter()

    result = {
        "symbol": symbol,
        "target_date": str(target_date),
        "fetch_elapsed": round(t1 - t0, 2),
        "summary_elapsed": round(t2 - t1, 2),
        "total_elapsed": round(t2 - t0, 2),
        "fetch_result": fetch_result,
        "summary_result": summary_result,
    }

    logger.info(f"[measure_pipeline] {result}")
    return result