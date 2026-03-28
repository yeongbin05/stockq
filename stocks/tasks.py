from celery import shared_task
from django.conf import settings
from datetime import datetime, timedelta,time, timezone as dt_timezone
from django.utils import timezone
from zoneinfo import ZoneInfo
import openai,json,logging
from django.contrib.auth import get_user_model
from stocks.services import upsert_news_for_symbol  
from stocks.models import Stock, News,Summary,FavoriteStock,SummaryGenerationLog,SummaryJob
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
    success_symbols = []
    failed_symbols = []
    enqueued_symbols = []

    today = timezone.localdate()

    for symbol in symbols:
        try:
            res = upsert_news_for_symbol(symbol, days=days)
            results.append({"symbol": symbol, **res})
            
            has_new_input = (
                res.get("created_news", 0) > 0 or
                res.get("linked_pairs", 0) > 0
            )

            summary_exists_today = Summary.objects.filter(
                stock__symbol__iexact=symbol,
                date=today,
            ).exists()

            if has_new_input and not summary_exists_today:
                stock = Stock.objects.get(symbol__iexact=symbol)
                _, created = SummaryJob.objects.get_or_create(
                    stock=stock,
                    date=today,
                    defaults={"status": SummaryJob.Status.PENDING},
                )
                if created:
                    enqueued_symbols.append(symbol)

            success_symbols.append(symbol)

        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})
            failed_symbols.append({"symbol": symbol, "error": str(e)})

    return {
        "results": results,
        "success_symbols": success_symbols,
        "failed_symbols": failed_symbols,
        "enqueued_symbols": enqueued_symbols,
    }

def estimate_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 4)

def _generate_summary_for_stock(job_id: int):
    job = SummaryJob.objects.select_related("stock").get(id=job_id)
    stock = job.stock
    symbol = stock.symbol
    target_date = job.date

    job.status = SummaryJob.Status.RUNNING
    job.started_at = timezone.now()
    job.save(update_fields=["status", "started_at"])
   
    if not settings.OPENAI_API_KEY:
        logger.error("[generate_summary] OPENAI_API_KEY not set")
        job.status = SummaryJob.Status.FAILED
        job.finished_at = timezone.now()
        job.error_message = "OpenAI API key not configured"
        job.save(update_fields=["status", "finished_at", "error_message"])
        return {"error": "OpenAI API key not configured", "job_id": job_id}

    openai.api_key = settings.OPENAI_API_KEY
   

    target_date, start_utc, end_utc = _get_utc_range_from_kst_date(target_date)

    news_query = News.objects.filter(
        stocks__symbol__iexact=symbol,
        published_at__gte=start_utc,
        published_at__lt=end_utc,
    ).order_by("-published_at")[:10]

    raw_count = news_query.count()

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

        job.status = SummaryJob.Status.NO_NEWS
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])
        return {"message": "No news found", "job_id": job_id}

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
        job.status = SummaryJob.Status.NO_RELEVANT_NEWS
        job.finished_at = timezone.now()
        job.save(update_fields=["status", "finished_at"])

        logger.info(f"[generate_summary] {symbol}: 관련 뉴스가 없어 요약 생략")
        return {"message": "No relevant news found", "job_id": job_id}
    
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
        job.status = SummaryJob.Status.FAILED
        job.finished_at = timezone.now()
        job.error_message = str(e)
        job.save(update_fields=["status", "finished_at", "error_message"])
        raise

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
        job.status = SummaryJob.Status.FAILED
        job.finished_at = timezone.now()
        job.error_message = f"json_parse_failed: {str(e)}"
        job.save(update_fields=["status", "finished_at", "error_message"])
        raise

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
    job.status = SummaryJob.Status.SUCCESS
    job.finished_at = timezone.now()
    job.save(update_fields=["status", "finished_at"])
    return {
        "job_id": job_id,
        "symbol": symbol,
        "status": "success",
        "elapsed": round(t1 - t0, 2),
        "raw_count": raw_count,
        "relevant_count": relevant_count,
    }

@shared_task(bind=True, max_retries=3)
def generate_summary_for_stock(self, job_id: int):
    try:
        return _generate_summary_for_stock(job_id)
    except SummaryJob.DoesNotExist:
        logger.error(f"[generate_summary] SummaryJob {job_id} not found")
        return {"error": f"SummaryJob {job_id} not found"}
    except Exception as e:
        logger.error(f"[generate_summary] job_id={job_id} 요약 실패: {e}")
        raise self.retry(exc=e, countdown=60)
    



def measure_pipeline_for_symbol(symbol: str, days: int = 1):
    t0 = perf_counter()

    fetch_result = upsert_news_for_symbol(symbol, days=days)
    t1 = perf_counter()

    kst = ZoneInfo("Asia/Seoul")
    target_date = timezone.now().astimezone(kst).date() - timedelta(days=days - 1)

    t2 = perf_counter()

    result = {
        "symbol": symbol,
        "target_date": str(target_date),
        "fetch_elapsed": round(t1 - t0, 2),
        "summary_elapsed": round(t2 - t1, 2),
        "total_elapsed": round(t2 - t0, 2),
        "fetch_result": fetch_result,
       
    }

    logger.info(f"[measure_pipeline] {result}")
    return result


