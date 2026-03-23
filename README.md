Load Testing (Baseline)

Endpoint

GET /api/stocks/summaries/ (JWT authenticated)

Tooling

k6 (local)

Server-side profiling: django-xbench (Server-Timing)

Scenario

10 Virtual Users

120 seconds

~1,180 requests

Error rate: 0%

Results

p95: 33.37 ms
p99: 119.76 ms
avg: 19.99 ms
max: 622.41 ms
throughput: ~9.7 req/s


Notes

Payload size ≈ 7.7 MB total

Majority of latency observed in tail (p99), not average

Baseline established before cache / response optimization

Why this matters

Focused on tail latency (p95/p99) rather than average

Test performed on authenticated, real production-like endpoint

Metrics recorded before optimization to enable clear before/after comparison

(선택) 한 줄 요약 버전

Established a reproducible load-testing baseline (p95/p99) for an authenticated read-heavy API before applying performance optimizations.





Daily News Summary Generation (LLM)

Test symbols: AAPL, NVDA, TSLA, MSFT  
News per symbol: 10

Average LLM summary time: ~8.5s


## Performance Benchmark

StockQ 뉴스 요약 파이프라인 성능 측정 결과

Test Environment
- LLM: OpenAI
- News per symbol: 10
- Pipeline: Finnhub → DB 저장 → LLM 요약 → Summary 저장

Measured Symbols
- AAPL
- NVDA
- TSLA
- MSFT

Results

| Symbol | Fetch Time | Summary Time | Total |
|------|------|------|------|
| AAPL | ~1.3s | ~7.4s | ~8.7s |
| NVDA | ~1.1s | ~6.3s | ~7.5s |
| TSLA | ~0.8s | ~11.0s | ~11.9s |
| MSFT | ~1.2s | ~7.9s | ~9.1s |

Average LLM Summary Time: **~8.46s**

Architecture:
Finnhub API → PostgreSQL 저장 → Celery 비동기 요약 → Summary 저장 → API 조회(ms 응답)


API Response Time

Endpoint: GET /api/stocks/summaries/?symbol=AAPL

Average Response Time: ~0.21s

Architecture:
뉴스 수집 및 LLM 요약은 Celery 배치에서 미리 생성하고,
API 요청 시에는 PostgreSQL에 저장된 Summary를 조회하여 ms 단위로 응답하도록 설계함.