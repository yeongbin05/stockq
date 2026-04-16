## Performance & Reliability

로컬기준
### 1. Read API Load Testing Baseline

주요 조회 API의 성능 개선 효과를 명확하게 비교하기 위해, 최적화 전에 재현 가능한 baseline을 먼저 측정했습니다.

#### Problem
요약 결과를 조회하는 API는 사용 빈도가 높은 read-heavy endpoint이기 때문에, 평균 응답속도만이 아니라 p95/p99 같은 tail latency 기준으로도 성능을 확인할 필요가 있었습니다.

#### Endpoint
- `GET /api/stocks/summaries/` (JWT authenticated)

#### Test Setup
- Tool: `k6`
- Server-side profiling: `django-xbench (Server-Timing)`
- 10 Virtual Users
- 120 seconds
- ~1,180 requests
- Error rate: `0%`

#### Result
- p95: `33.37 ms`
- p99: `119.76 ms`
- avg: `19.99 ms`
- max: `622.41 ms`
- throughput: `~9.7 req/s`

#### Notes
- Total payload size: `~7.7 MB`
- 평균 지연보다 tail latency(p95/p99)에 더 집중
- 캐시 및 응답 최적화 적용 전 baseline 확보

#### Why it matters
최적화 전 기준 성능을 먼저 수치화함으로써, 이후 캐시·쿼리·응답 구조 개선에 따른 before/after 비교가 가능하도록 했습니다.

**One-line summary**
- Established a reproducible p95/p99 baseline for an authenticated read-heavy API before optimization.

서버기준
1. Read API Load Testing in a Deployed Environment

주요 조회 API의 실제 운영 환경 성능을 확인하기 위해, 배포 서버 기준으로 read-heavy endpoint의 응답성과 tail latency를 측정했습니다.

Problem

GET /api/stocks/summaries/ 는 사용자가 자주 조회하는 read-heavy endpoint이기 때문에, 평균 응답속도뿐 아니라 p95/p99 같은 tail latency 기준으로도 안정성을 확인할 필요가 있었습니다.
특히 실제 사용자 요청 경로에서는 Cloudflare, Nginx, Gunicorn, Django, JWT 인증이 모두 포함되므로, 로컬이 아니라 배포 환경에서 측정한 수치가 더 중요했습니다.

Endpoint
GET /api/stocks/summaries/ (JWT authenticated)
Test Setup
Tool: k6
Server-side profiling: django-xbench (Server-Timing)
Environment:
Cloudflare
Nginx
Gunicorn
Django
Gunicorn workers: 2
Test scenarios:
10 VUs, 60s
50 VUs, 60s
Result

Scenario A — 10 VUs

throughput: 19.14 req/s
error rate: 0%
avg: 313.35 ms
p95: 345.24 ms
p99: 405.38 ms

Scenario B — 50 VUs

throughput: 31.49 req/s
error rate: 0.07%
avg: 651.79 ms
p95: 699.62 ms
p99: 7.64 s
Analysis
10 VUs 환경에서는 p95 345ms, 실패율 0%로 안정적으로 동작했습니다.
반면 50 VUs 환경에서는 throughput은 증가했지만, p95 699ms, p99 7.64s로 tail latency가 크게 악화되었습니다.
Gunicorn access log와 django-xbench 분석 결과, DB 시간은 대체로 2~4ms 수준으로 짧았고, 병목은 DB 자체보다 앱 처리 + 네트워크/프록시 구간 + sync worker 기반 queueing에 더 가까웠습니다.
실제로 낮은 부하에서는 정상 응답을 유지했지만, 높은 동시성에서는 일부 요청이 대기열에 쌓이며 tail latency가 증가하는 패턴을 확인했습니다.
Why it matters

단순히 “API가 빠르다”는 수준이 아니라, 실제 배포 환경에서 낮은~중간 부하에서는 안정적이지만 높은 동시성에서는 sync worker 구조의 한계로 tail latency가 증가한다는 점을 수치로 확인했습니다.
이를 통해 이후 개선 방향을 DB 튜닝보다 응답 경로 경량화, worker 모델 개선, concurrency 대응 쪽으로 더 명확히 잡을 수 있었습니다.

One-line summary

Verified that the authenticated read API is stable under moderate load, while higher concurrency exposes tail-latency issues caused by sync-worker queueing rather than DB bottlenecks.
---

### 2. LLM-Based Daily News Summary Pipeline Benchmark

뉴스 수집부터 요약 저장까지의 전체 파이프라인 시간을 측정해, 어떤 구간이 병목인지 확인했습니다.

#### Problem
사용자 요청 시점마다 LLM 요약을 생성하면 응답 시간이 길어지고, 읽기 API 성능이 크게 저하될 수 있습니다.  
따라서 생성 비용이 큰 작업은 배치로 분리하고, 실제 병목이 어디인지 먼저 측정할 필요가 있었습니다.

#### Architecture
Finnhub API → PostgreSQL 저장 → Celery 비동기 요약 → Summary 저장 → API 조회

#### Test Setup
- LLM: OpenAI
- News per symbol: `10`
- Measured symbols:
  - `AAPL`
  - `NVDA`
  - `TSLA`
  - `MSFT`

#### Result

| Symbol | Fetch Time | Summary Time | Total Time |
|--------|------------|--------------|------------|
| AAPL   | ~1.3s      | ~7.4s        | ~8.7s      |
| NVDA   | ~1.1s      | ~6.3s        | ~7.5s      |
| TSLA   | ~0.8s      | ~11.0s       | ~11.9s     |
| MSFT   | ~1.2s      | ~7.9s        | ~9.1s      |

- Average LLM summary time: `~8.46s`

#### Analysis
- 뉴스 수집은 대체로 `1초 내외`로 완료
- 전체 파이프라인 시간 대부분은 LLM 요약 구간이 차지
- 요청 경로에서 직접 생성하는 구조보다, 미리 생성해 저장하는 구조가 훨씬 유리함을 확인

#### Why it matters
생성 비용이 큰 작업을 API 요청 경로에서 제거하고, 조회와 생성을 분리해야 한다는 설계 근거를 수치로 확인했습니다.

---

### 3. Fast Read Path via Precomputed Summaries

LLM 생성 비용을 사용자 요청 경로에서 제거하기 위해, 배치 선생성 구조를 적용했습니다.

#### Problem
LLM 요약 생성은 평균 `~8.46s`가 걸리므로, 이를 사용자 요청 시점에 수행하면 API 응답성이 크게 저하됩니다.

#### Solution
- 뉴스 수집 및 요약 생성은 Celery 배치에서 미리 수행
- 생성된 결과를 PostgreSQL `Summary` 테이블에 저장
- API 요청 시에는 저장된 결과만 조회

#### Endpoint
- `GET /api/stocks/summaries/?symbol=AAPL`

#### Result
- Average response time: `~0.21s`

#### Why it matters
비용이 큰 생성 작업과 빈도가 높은 조회 작업을 분리함으로써, 사용자 요청 경로를 가볍게 유지하고 빠른 응답을 제공할 수 있게 했습니다.

**One-line summary**
- Moved expensive LLM generation out of the request path and served precomputed summaries for sub-second read performance.

---

### 4. External API Rate Limit Handling

외부 뉴스 API 사용 시 안정성을 높이기 위해 rate limiting과 retry 로직을 적용했습니다.

#### Problem
Finnhub 같은 외부 API는 호출 제한이 존재하므로, 단순 반복 호출만으로는 배치 작업 중 `429 Too Many Requests`가 발생할 수 있습니다.  
또한 여러 worker가 동시에 동작하는 환경에서는 단순 `sleep` 방식만으로 호출 속도를 안정적으로 제어하기 어렵습니다.

#### Solution
- Redis + Lua token bucket 기반 rate limiting 적용
- Finnhub 호출 전 `wait_for_slot()`으로 사전 제한
- `429 Too Many Requests` 및 네트워크 오류에 대해 exponential backoff retry 적용
- Celery 배치에서 뉴스 수집 → relevance filtering → LLM 요약 → DB 저장까지 전체 흐름 검증

#### Verified Flow
- `fetch_favorite_news` 실행 확인
- `daily_news_summary_batch` 실행 확인
- `generate_summary_for_stock` 실행 확인
- relevance filtering 확인
  - `raw_count = 10`
  - `relevant_count = 5`
- OpenAI 호출 및 저장 성공 확인

#### Why it matters
외부 API 제한으로 인해 전체 배치가 불안정해지는 문제를 줄이고, 분산 환경에서도 더 일관된 호출 제어가 가능하도록 개선했습니다.

**One-line summary**
- Improved batch stability with Redis Lua rate limiting and exponential backoff retry for external API calls.

---

### 5. Relevance Filtering Before LLM Input

LLM에 전달하는 뉴스 수를 줄여 불필요한 입력을 줄이고, 요약 품질과 비용 효율을 함께 개선했습니다.

#### Problem
수집된 뉴스 전체를 그대로 LLM에 전달하면, 종목과 직접 관련 없는 기사까지 포함되어 입력 토큰이 증가하고 요약 품질도 흔들릴 수 있습니다.

#### Solution
- headline 기준 relevance scoring 적용
- 기업명 / 심볼 / ORG entity / finance keyword 기반 점수화
- 관련 기사만 선별해 LLM 입력으로 사용

#### Verified Example
- `symbol=AAPL`
- `raw_count=10`
- `relevant_count=5`

#### Why it matters
LLM 입력량을 줄이고, 실제 종목과 관련된 기사 중심으로 요약하도록 만들어 품질과 비용 효율을 동시에 개선할 수 있는 기반을 마련했습니다.


## Production Validation

- SummaryJob end-to-end pipeline verified in production
- No duplicate summaries for same (stock, date)
- Status tracking: success / failed / no_relevant_news
- Token optimization: 400 → 297 (~25% reduction)
- No stuck jobs observed


### Read API 부하 테스트 결과

`GET /api/stocks/summaries/`를 대상으로 k6 부하 테스트를 수행했다.  
테스트 당시 DB에는 실제 요약 데이터가 59건 존재했고, 최근 데이터는 2026-04-09 기준으로 AAPL, GOOGL, META, AMZN, TSLA 등이 저장되어 있었다.

- 3 VU, 30초: p95 87.52ms / p99 286.99ms / 에러율 0%
- 10 VU, 30초: p95 49.15ms / p99 446.83ms / 에러율 0% / 약 34.4 RPS
- 20 VU, 30초: p95 187.3ms / p99 242.33ms / 에러율 0% / 약 53.9 RPS

측정 결과, 핵심 조회 API인 `/api/stocks/summaries/`는 20 VU 수준까지 안정적으로 동작했으며 에러 없이 응답했다.  
따라서 현재 구조에서 조회 API는 주요 병목으로 보이지 않았고, 이후 성능 개선 우선순위는 Celery 기반 요약 파이프라인 측정 및 병목 분석에 두기로 했다.


Summary execution pipeline benchmark
total p95: 8.16s
llm p95: 6.75s
relevance p95: 1.92s
Burst dispatch observation
queue_wait p95: 37.48s
원인: 잡 17건이 한 번에 디스패치되며 worker queueing 발생

worker concurrency=2 환경에서 SummaryJob 5개를 재현했을 때, 기존 burst dispatch는 작업을 한 번에 enqueue해 queue_wait가 0.101s, 0.122s, 4.922s, 5.286s, 9.289s까지 증가했다. 이를 inflight-slot 기반 dispatch로 변경한 뒤 동일 재현에서 queue_wait를 0.235s, 0.238s, 0.157s, 0.165s, 0.031s 수준으로 유지했다.
