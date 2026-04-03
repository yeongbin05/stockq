import openai,json,logging,uuid
from celery import shared_task
from datetime import datetime, timedelta,time, timezone as dt_timezone
from django.db import transaction
from django.db.models import Q
from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from stocks.services import upsert_news_for_symbol  
from stocks.models import Stock, News,Summary,FavoriteStock,SummaryGenerationLog,SummaryJob
from stocks.utils import score_news_relevance
from stocks.rate_limit import get_openai_bucket
from time import perf_counter
from zoneinfo import ZoneInfo
from stocks.rate_limit import get_openai_bucket, get_finnhub_bucket
logger = logging.getLogger(__name__)
User = get_user_model()

def _is_current_lease(job_id: int, lease_token: str) -> bool:
    return SummaryJob.objects.filter(
        id=job_id,
        status=SummaryJob.Status.RUNNING,
        lease_token=lease_token,
    ).exists()

def _get_utc_range_from_kst_date(target_date=None):
    kst = ZoneInfo("Asia/Seoul")

    if target_date is None:
        target_date = timezone.now().astimezone(kst).date()

    start_kst = datetime.combine(target_date, time.min, tzinfo=kst)
    end_kst = start_kst + timedelta(days=1)

    start_utc = start_kst.astimezone(ZoneInfo("UTC"))
    end_utc = end_kst.astimezone(ZoneInfo("UTC"))

    return target_date, start_utc, end_utc


QUEUE_START_TIMEOUT = timedelta(minutes=2)
RUNNING_EXEC_TIMEOUT = timedelta(minutes=10)
MAX_STUCK_RECOVERY_RETRIES = 3

RETRY_BACKOFF_MINUTES = {
    0: 1,
    1: 5,
    2: 15,
}


def _get_retry_backoff_minutes(retry_count: int) -> int:
    return RETRY_BACKOFF_MINUTES.get(retry_count, 15)


@shared_task
def recover_stuck_summary_jobs():
    now = timezone.now()

    queue_start_deadline = now - QUEUE_START_TIMEOUT
    running_exec_deadline = now - RUNNING_EXEC_TIMEOUT

    stuck_jobs = list(
        SummaryJob.objects.filter(
            status=SummaryJob.Status.RUNNING,
            finished_at__isnull=True,
        ).filter(
            Q(
                started_at__isnull=True,
                dispatched_at__isnull=False,
                dispatched_at__lte=queue_start_deadline,
            ) |
            Q(
                started_at__isnull=False,
                started_at__lte=running_exec_deadline,
            )
        ).values("id", "retry_count", "lease_token")
    )

    recovered_job_ids = []
    failed_job_ids = []

    for job in stuck_jobs:
        job_id = job["id"]
        retry_count = job["retry_count"]
        lease_token = job["lease_token"]

        if lease_token is None:
            continue

        if retry_count >= MAX_STUCK_RECOVERY_RETRIES:
            updated = SummaryJob.objects.filter(
                id=job_id,
                status=SummaryJob.Status.RUNNING,
                lease_token=lease_token,
                finished_at__isnull=True,
            ).update(
                status=SummaryJob.Status.FAILED,
                finished_at=now,
                error_message="stuck timeout exceeded max retries",
            )
            if updated:
                failed_job_ids.append(job_id)
            continue

        backoff_minutes = _get_retry_backoff_minutes(retry_count)

        updated = SummaryJob.objects.filter(
            id=job_id,
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
            finished_at__isnull=True,
        ).update(
            status=SummaryJob.Status.RETRY_WAIT,
            retry_count=retry_count + 1,
            retry_at=now + timedelta(minutes=backoff_minutes),
            started_at=None,
            finished_at=None,
            dispatched_at=None,
            lease_token=None,
            error_message="stuck timeout recovery",
        )

        if updated:
            recovered_job_ids.append(job_id)

    return {
        "recovered_job_ids": recovered_job_ids,
        "failed_job_ids": failed_job_ids,
    }


from time import perf_counter

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
            t_symbol_start = perf_counter()

            t_bucket_start = perf_counter()
            if settings.FINNHUB_BUCKET_ENABLED:
                bucket = get_finnhub_bucket()
                bucket_result = bucket.consume(tokens=1)

                if not bucket_result.allowed:
                    t_bucket_end = perf_counter()
                    logger.info(
                        "[fetch_favorite_news_breakdown] symbol=%s bucket=%.3fs upsert=0.000s enqueue=0.000s total=%.3fs status=rate_limited retry_after=%s",
                        symbol,
                        t_bucket_end - t_bucket_start,
                        t_bucket_end - t_symbol_start,
                        bucket_result.retry_after_seconds,
                    )
                    results.append({
                        "symbol": symbol,
                        "status": "rate_limited",
                        "retry_after": bucket_result.retry_after_seconds,
                        "error": f"Rate limit wait timeout: {symbol}",
                    })
                    failed_symbols.append({
                        "symbol": symbol,
                        "error": f"Rate limit wait timeout: {symbol}",
                    })
                    continue
            t_bucket_end = perf_counter()

            t_upsert_start = perf_counter()
            res = upsert_news_for_symbol(symbol, days=days)
            t_upsert_end = perf_counter()

            t_enqueue_start = perf_counter()
            results.append({"symbol": symbol, **res})

            has_new_input = (
                res.get("created_news", 0) > 0 or
                res.get("linked_pairs", 0) > 0
            )

            summary_exists_today = Summary.objects.filter(
                stock__symbol__iexact=symbol,
                date=today,
            ).exists()

            created = False
            if has_new_input and not summary_exists_today:
                stock = Stock.objects.get(symbol__iexact=symbol)
                _, created = SummaryJob.objects.get_or_create(
                    stock=stock,
                    date=today,
                    defaults={"status": SummaryJob.Status.PENDING},
                )
                if created:
                    enqueued_symbols.append(symbol)
            t_enqueue_end = perf_counter()

            logger.info(
                "[fetch_favorite_news_breakdown] symbol=%s bucket=%.3fs upsert=%.3fs enqueue=%.3fs total=%.3fs created_news=%s linked_pairs=%s enqueued=%s",
                symbol,
                t_bucket_end - t_bucket_start,
                t_upsert_end - t_upsert_start,
                t_enqueue_end - t_enqueue_start,
                t_enqueue_end - t_symbol_start,
                res.get("created_news", 0),
                res.get("linked_pairs", 0),
                created,
            )

            success_symbols.append(symbol)

        except Exception as e:
            logger.exception("[fetch_favorite_news] symbol=%s failed: %s", symbol, e)
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

def _generate_summary_for_stock(job_id: int,lease_token: str):
    job = SummaryJob.objects.select_related("stock").get(id=job_id)
    stock = job.stock
    symbol = stock.symbol
    target_date = job.date
   
    if not settings.OPENAI_API_KEY:
        logger.error("[generate_summary] OPENAI_API_KEY not set")
        SummaryJob.objects.filter(
            id=job_id,
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
        ).update(
            status=SummaryJob.Status.FAILED,
            finished_at=timezone.now(),
            error_message="OpenAI API key not configured",
        )
        return {"error": "OpenAI API key not configured", "job_id": job_id}

    openai.api_key = settings.OPENAI_API_KEY
   

    target_date, start_utc, end_utc = _get_utc_range_from_kst_date(target_date)

    t_news_query_start = perf_counter()
    news_items = list(
        News.objects.filter(
            stocks__symbol__iexact=symbol,
            published_at__gte=start_utc,
            published_at__lt=end_utc,
        ).order_by("-published_at")[:10]
    )
    t_news_query_end = perf_counter()

    raw_count = len(news_items)

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

        SummaryJob.objects.filter(
            id=job_id,
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
        ).update(
            status=SummaryJob.Status.NO_NEWS,
            finished_at=timezone.now(),
            error_message="",
        )
        return {"message": "No news found", "job_id": job_id}

    t_relevance_start = perf_counter()
    scored_news = []
    for news in news_items:
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
    t_relevance_end = perf_counter()
    logger.info(
        f"[generate_summary] symbol={symbol} raw_count={raw_count} relevant_count={relevant_count}"
    )

    
    t_prompt_start = perf_counter()
    kst = timezone.get_fixed_timezone(9 * 60)

    all_news_texts = []
    for news in news_items:
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
    t_prompt_end = perf_counter()

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
        SummaryJob.objects.filter(
            id=job_id,
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
        ).update(
            status=SummaryJob.Status.NO_RELEVANT_NEWS,
            finished_at=timezone.now(),
            error_message="",
        )

        logger.info(f"[generate_summary] {symbol}: 관련 뉴스가 없어 요약 생략")
        return {"message": "No relevant news found", "job_id": job_id}
    
    t0 = perf_counter()
    if not _is_current_lease(job_id, lease_token):
        return {
            "job_id": job_id,
            "status": "stale_before_llm",
        }
    try:
        if settings.OPENAI_BUCKET_ENABLED:
            bucket = get_openai_bucket()
            bucket_result = bucket.consume(tokens=1)

            if not bucket_result.allowed:
                retry_at = timezone.now() + timedelta(seconds=bucket_result.retry_after_seconds)

                logger.warning(
                    "[generate_summary] OpenAI bucket exceeded for %s. retry_after=%ss remaining=%s",
                    stock.symbol,
                    bucket_result.retry_after_seconds,
                    bucket_result.remaining_tokens,
                )

                SummaryJob.objects.filter(
                    id=job_id,
                    status=SummaryJob.Status.RUNNING,
                    lease_token=lease_token,
                ).update(
                    status=SummaryJob.Status.RETRY_WAIT,
                    retry_at=retry_at,
                    started_at=None,
                    finished_at=None,
                    dispatched_at=None,
                    lease_token=None,
                    error_message="rate limited by openai bucket",
                )

                return {
                    "job_id": job_id,
                    "status": "rate_limited",
                    "retry_after": bucket_result.retry_after_seconds,
                }
        t_llm_start = perf_counter()
        response = openai.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=1200,
            temperature=0.2
        )
        t_llm_end = perf_counter()
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
        SummaryJob.objects.filter(
            id=job_id,
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
        ).update(
            status=SummaryJob.Status.FAILED,
            finished_at=timezone.now(),
            error_message=str(e),
        )
        raise
    t_parse_save_start = perf_counter()
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
        SummaryJob.objects.filter(
            id=job_id,
            status=SummaryJob.Status.RUNNING,
            lease_token=lease_token,
        ).update(
            status=SummaryJob.Status.FAILED,
            finished_at=timezone.now(),
            error_message=f"json_parse_failed: {str(e)}",
        )
        raise
    
    if not _is_current_lease(job_id, lease_token):
        return {
            "job_id": job_id,
            "status": "stale_after_llm",
        }


    Summary.objects.update_or_create(
        stock=stock,
        date=target_date,
        defaults={
            "summary": summary_json
        }
    )

    t_parse_save_end = perf_counter()
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

    total_elapsed = t_parse_save_end - t_news_query_start

    logger.info(
        "[generate_summary_breakdown] symbol=%s news_query=%.3fs relevance=%.3fs prompt_build=%.3fs llm=%.3fs parse_save=%.3fs total=%.3fs",
        symbol,
        t_news_query_end - t_news_query_start,
        t_relevance_end - t_relevance_start,
        t_prompt_end - t_prompt_start,
        t_llm_end - t_llm_start,
        t_parse_save_end - t_parse_save_start,
        total_elapsed,
    )
    SummaryJob.objects.filter(
        id=job_id,
        status=SummaryJob.Status.RUNNING,
        lease_token=lease_token,
    ).update(
        status=SummaryJob.Status.SUCCESS,
        finished_at=timezone.now(),
        error_message="",
    )
    return {
        "job_id": job_id,
        "symbol": symbol,
        "status": "success",
        "elapsed": round(t1 - t0, 2),
        "raw_count": raw_count,
        "relevant_count": relevant_count,
    }

@shared_task(bind=True)
def generate_summary_for_stock(self, job_id: int, lease_token: str):
    started = SummaryJob.objects.filter(
        id=job_id,
        status=SummaryJob.Status.RUNNING,
        lease_token=lease_token,
        started_at__isnull=True,
    ).update(started_at=timezone.now())

    if started == 0:
        return {
            "job_id": job_id,
            "status": "stale_or_already_started",
        }
    job = SummaryJob.objects.select_related("stock").get(id=job_id)
    queue_wait = None
    if job.dispatched_at:
        queue_wait = (
            (job.started_at - job.dispatched_at).total_seconds()
            if job.started_at and job.dispatched_at
            else 0.0
        )

    logger.info(
        "[generate_summary_for_stock] worker_started job_id=%s symbol=%s queue_wait=%.3f",
        job.id,
        job.stock.symbol,
        queue_wait or 0.0,
    )
    return _generate_summary_for_stock(job_id, lease_token)

@shared_task
def dispatch_summary_jobs(limit: int = 20):
    now = timezone.now()
    dispatch_targets = []

    with transaction.atomic():
        jobs = list(
            SummaryJob.objects
            .select_for_update(skip_locked=True)
            .filter(
                Q(status=SummaryJob.Status.PENDING) |
                Q(
                    status=SummaryJob.Status.RETRY_WAIT,
                    retry_at__lte=now,
                )
            )
            .order_by("created_at")[:limit]
        )

        for job in jobs:
            logger.info("[dispatch_summary_jobs] picked job_id=%s", job.id)
            lease_token = uuid.uuid4()

            job.status = SummaryJob.Status.RUNNING
            job.dispatched_at = now
            job.started_at = None
            job.finished_at = None
            job.retry_at = None
            job.lease_token = lease_token
            job.error_message = ""

            job.save(
                update_fields=[
                    "status",
                    "dispatched_at",
                    "started_at",
                    "finished_at",
                    "retry_at",
                    "lease_token",
                    "error_message",
                    "updated_at",
                ]
            )

            dispatch_targets.append((job.id, str(lease_token)))

    for job_id, lease_token in dispatch_targets:
        generate_summary_for_stock.delay(job_id, lease_token)

    return {
        "dispatched_count": len(dispatch_targets),
        "job_ids": [job_id for job_id, _ in dispatch_targets],
    }