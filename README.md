# Performance & Reliability

## 문제

StockQ는 미국 주식 뉴스를 수집하고 LLM으로 요약하는 서비스입니다.  
초기 구조에서 요약 생성을 요청 경로에 직접 두면, 사용자 응답 시간이 길어지고 read API 안정성이 쉽게 흔들릴 수 있었습니다.

또한 운영 관점에서는 다음 문제가 있었습니다.

- LLM 호출이 비용이 크고 latency 편차가 커서 요청 시점 처리에 부적합함
- Finnhub 같은 외부 API는 rate limit가 있어 배치 작업이 쉽게 불안정해질 수 있음
- pending 잡을 한 번에 enqueue하는 burst dispatch 방식에서는 worker queue 적체가 발생할 수 있음
- 비동기 파이프라인은 단순 API 로그만으로는 병목과 적체 지점을 파악하기 어려움

즉, StockQ의 핵심 문제는  
**“LLM 기반 생성 작업을 요청 경로와 분리하지 않으면 성능과 운영 안정성을 동시에 확보하기 어렵다”**는 점이었습니다.

---

## 왜 이 구조를 선택했는지

이 문제를 해결하기 위해 생성과 조회를 분리한 **비동기 파이프라인 구조**를 선택했습니다.

**Finnhub API → PostgreSQL 저장 → SummaryJob 생성 → dispatcher가 capacity/inflight 기준으로 dispatch → Celery worker가 `generate_summary_for_stock(job_id, lease_token)` 처리 → Summary 저장 → Read API 조회**

이 구조를 선택한 이유는 다음과 같습니다.

- 비용이 큰 LLM 생성 작업을 사용자 요청 경로에서 분리할 수 있음
- read API는 저장된 결과만 조회하므로 빠르고 안정적으로 유지할 수 있음
- SummaryJob 상태를 기준으로 pending / running / success / failed / no_relevant_news 등을 추적할 수 있어 운영이 쉬워짐
- 외부 API 호출 제어, 재시도, backpressure, stuck job recovery 같은 운영 정책을 작업 파이프라인에 녹일 수 있음

즉, 단순히 “비동기로 바꿨다”가 아니라  
**LLM 생성 비용, 외부 API 제약, queue 적체, 운영 가시성 문제를 함께 다루기 위해 이 구조를 선택했습니다.**

---

## 핵심 개선

### 1. SummaryJob + Celery 기반 비동기 생성 파이프라인으로 재구성
LLM 요약 생성을 요청 경로에서 분리하고, 종목/날짜 단위 SummaryJob을 생성한 뒤 dispatcher가 capacity/inflight 기준으로 작업을 선별해 Celery worker에 전달하도록 바꿨습니다.
이로 인해 일반 사용자용 summaries API는 저장된 summary만 조회하는 read API로 유지되고, 생성 비용과 사용자 응답 경로를 분리할 수 있었습니다.

### 2. Stage-level timing으로 실제 병목 구간 실측
파이프라인에 stage timing을 추가해 어떤 구간이 실제 병목인지 측정했습니다.  
그 결과 DB나 dispatcher보다 **LLM 호출 단계가 주 병목**이라는 점을 확인했고, 이후 최적화 우선순위를 DB가 아니라 **LLM latency, 입력량 제어, queue 운영**에 두었습니다.

### 3. Burst dispatch를 inflight-slot 기반 dispatch로 개선
기존에는 pending 잡을 한 번에 enqueue하는 burst dispatch 방식 때문에 worker queue 적체가 발생했습니다.  
이를 worker capacity 기준으로만 dispatch하는 **inflight-slot 방식**으로 바꾸어 admission control / backpressure를 적용했습니다.

### 4. Redis Lua 기반 rate limiting으로 외부 API 호출 안정화
Finnhub 호출 전 `wait_for_slot()`을 두고, Redis + Lua token bucket으로 외부 API 호출량을 제어했습니다.  
또한 `429 Too Many Requests` 및 네트워크 오류에 대해 exponential backoff retry를 적용해 재시도 폭주를 줄였습니다.

### 5. Relevance filtering으로 LLM 입력량 최적화
관련 기사만 선별해 LLM에 전달하도록 하여 입력 토큰을 줄였습니다.  
이를 통해 단순 비용 절감뿐 아니라, 불필요한 기사로 인한 요약 품질 저하도 함께 줄이도록 설계했습니다.

### 6. 운영 관측성 강화
SummaryJob 상태, queue wait, total elapsed, stuck job 여부를 메트릭으로 노출하고, Grafana 대시보드와 Slack 알람으로 비동기 파이프라인의 상태를 운영자가 바로 확인할 수 있도록 구성했습니다.

---

## 결과 / 지표

### 병목 분석
- **total p95: 8.16s**
- **llm p95: 6.75s**
- **relevance p95: 1.92s**

실측 결과, 주요 병목은 DB가 아니라 **LLM 요약 단계**였습니다.  
이를 통해 최적화 방향을 DB 튜닝보다 **LLM 비용, latency, 입력량 제어** 쪽으로 좁힐 수 있었습니다.

### Queue wait 개선
기존 burst dispatch에서는 실제로 queue 적체가 발생했습니다.

**기존 burst dispatch**
- `0.101s`
- `0.122s`
- `4.922s`
- `5.286s`
- `9.289s`

**개선 후 inflight-slot dispatch**
- `0.235s`
- `0.238s`
- `0.157s`
- `0.165s`
- `0.031s`

즉, worker capacity에 맞게 작업을 dispatch하도록 바꾼 뒤  
**queue 적체를 크게 줄이고 운영 안정성을 높였습니다.**

### Read path 성능
배포 환경에서 `GET /api/stocks/summaries/`를 k6로 측정한 결과:

- **3 VU, 30초**: `p95 87.52ms`, `p99 286.99ms`, `에러율 0%`
- **10 VU, 30초**: `p95 49.15ms`, `p99 446.83ms`, `에러율 0%`, `약 34.4 RPS`
- **20 VU, 30초**: `p95 187.3ms`, `p99 242.33ms`, `에러율 0%`, `약 53.9 RPS`

테스트 당시 실제 summary 데이터 59건이 존재했고,  
read API는 **20 VU까지 안정적으로 동작**했습니다.

### Read path 최적화: Serialization 병목 제거
또한 별도의 read path인 **주식 검색 API**에서는 SQL 자체보다 **직렬화와 응답 크기**가 병목이 되는 문제도 확인했습니다.  
실제 `?q=a` 검색 요청에서 전체 응답 시간은 약 **3.3초**, payload 크기는 **5.6MB**까지 증가했지만, Django Debug Toolbar 기준 SQL 실행 시간은 **6.11ms**에 불과했습니다.

즉, 주요 병목은 DB 쿼리가 아니라 **대량 객체 직렬화, Browsable API 렌더링, 과도한 응답 payload**에 있었습니다.  
이를 해결하기 위해 검색 API에 `CursorPagination(page_size=20)`을 도입해 한 번에 필요한 데이터만 반환하도록 변경했습니다.

그 결과 JSON 기준 응답 시간은 **1,750ms → 37ms**, 응답 크기는 **2.2MB → 약 30kB**로 줄었고, 최대 **약 47배**의 성능 개선을 확인했습니다.

이 경험을 통해 read path 성능은 SQL 시간만으로 판단할 수 없고, **serializer 처리 비용, payload 크기, TTFB**까지 함께 봐야 한다는 점을 확인했습니다.

### ORM 최적화: Django N+1 제거
또한 read path에서는 Django ORM/DRF 사용 시 발생할 수 있는 N+1 문제도 점검했습니다.  
실제 주식 검색 API에서 `SerializerMethodField`로 즐겨찾기 여부나 최신 가격 정보를 객체별로 계산하면, 결과 개수에 비례해 추가 쿼리가 발생할 수 있었습니다.

이를 즐겨찾기 여부는 `annotate + Exists`, 최신 가격과 등락률은 `Subquery` annotation으로 변경하고, serializer가 annotated field를 직접 사용하도록 수정해 검색 결과 직렬화 중 per-object DB 조회가 발생하지 않도록 했습니다.
그 결과 응답 시간도 **0.26s → 0.02s**로 개선했습니다.

이 경험을 통해 ORM은 생산성이 높지만, 추상화 뒤에서 N+1 같은 비효율이 쉽게 숨어들 수 있다는 점을 확인했고,  
이후 read path에서는 쿼리 수와 serializer 접근 패턴을 함께 점검하는 기준을 적용했습니다.

### 입력 최적화
- `raw_count = 10`
- `relevant_count = 5`
- 입력 token `400 → 297` (**약 25% 감소**)

이를 통해 LLM 비용 효율과 입력 품질을 함께 개선했습니다.

### Production validation
production 환경에서 SummaryJob 생성 → dispatch → worker 처리 → summary 저장까지 end-to-end로 검증했습니다.

- 동일 `(stock, date)` 중복 생성 방지
- `success`, `failed`, `no_relevant_news` 등 상태 기반 추적
- stuck job 미발생 확인
- 비동기 생성 파이프라인이 실제 운영 환경에서 동작함을 검증

---

## 로컬 실행

### 1. 환경 변수 준비

```bash
cp .env.example .env
```

`.env.example`에는 로컬 실행에 필요한 Django, OpenAI/Finnhub, Postgres, Redis/Celery 예시 값이 들어 있습니다. 실제 외부 API를 호출하려면 `OPENAI_API_KEY`, `FINNHUB_API_KEY`를 본인 키로 교체하세요.

### 2. Docker로 실행

```bash
docker compose -f docker-compose.dev.yml up -d --build db redis web celery_worker celery_beat
docker compose -f docker-compose.dev.yml exec web python manage.py migrate
docker compose -f docker-compose.dev.yml exec web python manage.py runserver 0.0.0.0:8000
```

개발용 compose의 `web` 서비스는 기본적으로 대기 상태로 실행되므로, 위처럼 `runserver`를 명시적으로 실행합니다.

### 3. 로컬 Python으로 실행

```bash
pip install -r requirements-dev.txt
python manage.py migrate
python manage.py runserver
```

기본 로컬 설정은 `pytest.ini`와 `stockq/settings/local.py` 기준으로 SQLite와 로컬 개발 설정을 사용합니다.

## 테스트 계정

포트폴리오 확인용 계정은 다음 값을 사용합니다.

- email: `test1@test.com`
- password: `1111`

로컬 DB에 계정이 없다면 회원가입 API 또는 Django shell로 동일한 계정을 생성한 뒤 사용하세요.

## 핵심 API 사용 흐름

아래 예시는 `http://localhost:8000` 기준입니다. 인증이 필요한 API는 `Authorization: Bearer <access>` 헤더를 포함합니다.

### 1. 로그인

```bash
curl -X POST http://localhost:8000/api/auth/token/ \
  -H "Content-Type: application/json" \
  -d '{"email":"test1@test.com","password":"1111"}'
```

응답의 `access` 값을 이후 요청의 Bearer token으로 사용합니다.

### 2. 종목 검색

```bash
curl "http://localhost:8000/api/stocks/search/?q=AAPL" \
  -H "Authorization: Bearer <access>"
```

검색 API는 cursor pagination을 사용하며, 즐겨찾기 여부와 최신 가격 정보는 DB annotation으로 계산해 serializer 단계의 per-object 조회를 피합니다.

### 3. 즐겨찾기 추가 / 삭제

```bash
curl -X POST http://localhost:8000/api/stocks/favorites/ \
  -H "Authorization: Bearer <access>" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL"}'

curl -X DELETE http://localhost:8000/api/stocks/favorites/AAPL/ \
  -H "Authorization: Bearer <access>"
```

### 4. 저장된 요약 조회

```bash
curl http://localhost:8000/api/stocks/summaries/ \
  -H "Authorization: Bearer <access>"

curl http://localhost:8000/api/stocks/summaries/AAPL/ \
  -H "Authorization: Bearer <access>"
```

일반 사용자용 summaries API는 저장된 Summary를 조회하는 read API입니다. 요약 생성은 사용자 요청 경로에서 직접 LLM을 호출하지 않고, `SummaryJob` 생성 후 dispatcher가 capacity/inflight 기준으로 dispatch하고 Celery worker가 `generate_summary_for_stock(job_id, lease_token)`을 처리하는 공식 비동기 파이프라인에서 수행됩니다.

## 테스트 실행

CI와 동일한 Postgres/Redis 환경에서 테스트하려면 Docker Compose를 사용합니다.

```bash
docker compose -f docker-compose.ci.yml up --build --abort-on-container-exit --exit-code-from web
```

로컬 Python 환경에서 실행하려면 의존성을 설치한 뒤 pytest를 실행합니다.

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

`pytest.ini`는 일반 로컬 실행을 위해 `stockq.settings.local`을 기본값으로 사용하고, Docker CI에서는 `DJANGO_SETTINGS_MODULE=stockq.settings.ci`로 덮어써서 Postgres/Redis 기반 테스트를 실행합니다.


## 테스트 실행

CI와 동일한 Postgres/Redis 환경에서 테스트하려면 Docker Compose를 사용합니다.

```bash
docker compose -f docker-compose.ci.yml up --build --abort-on-container-exit --exit-code-from web
```

로컬 Python 환경에서 실행하려면 의존성을 설치한 뒤 pytest를 실행합니다.

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

`pytest.ini`는 일반 로컬 실행을 위해 `stockq.settings.local`을 기본값으로 사용하고, Docker CI에서는 `DJANGO_SETTINGS_MODULE=stockq.settings.ci`로 덮어써서 Postgres/Redis 기반 테스트를 실행합니다.

## SLI / SLO

StockQ는 단순히 기능이 동작하는 수준을 넘어서,  
read API와 비동기 summary pipeline을 **측정 가능한 운영 지표**로 관리하는 것을 목표로 했습니다.

현재는 SLA(외부 고객 대상 보장)까지 두기보다는,  
내부 운영 기준인 **SLI / SLO**를 먼저 정의하고 Grafana / Prometheus / Slack 알림과 연결하는 방식으로 관리합니다.

### 1. Read API

| 항목 | SLI | SLO | 비고 |
|---|---|---|---|
| Read API latency | `GET /api/stocks/summaries/`의 p95 latency | **p95 < 300ms** | route 기준 커스텀 histogram 메트릭으로 측정 |
| Read API error rate | `GET /api/stocks/summaries/`의 5xx error rate | **< 1%** | route / method / status 라벨 기반 커스텀 counter 메트릭으로 측정 |
| Read API request rate | `GET /api/stocks/summaries/`의 최근 5분 요청량 | 참고 지표 | read path 사용량과 트래픽 변화를 보기 위한 운영 지표 |
| Read API successful requests | `GET /api/stocks/summaries/`의 최근 5분 성공 요청 수 | 참고 지표 | 실제 성공 호출 여부와 테스트 검증용 보조 지표 

### 2. Search API

| 항목 | SLI | SLO | 비고 |
|---|---|---|---|
| Search API latency | `GET /api/stocks/search/`의 p95 latency | **p95 < 300ms** | route 기준 커스텀 histogram 메트릭으로 측정 |
| Search API error rate | `GET /api/stocks/search/`의 5xx error rate | **< 1%** | route / method / status 라벨 기반 커스텀 counter 메트릭으로 측정 |
| Search API request rate | `GET /api/stocks/search/`의 최근 5분 요청량 | 참고 지표 | 검색 사용량과 트래픽 변화를 보기 위한 운영 지표 |
| Search API successful requests | `GET /api/stocks/search/`의 최근 5분 성공 요청 수 | 참고 지표 | 실제 성공 호출 여부와 테스트 검증용 보조 지표 ||

### 3. Summary Pipeline

| 항목 | SLI | SLO | 비고 |
|---|---|---|---|
| SummaryJob success rate | `success / (success + failed)` | **> 95%** | 요약이 실제로 정상 생성되는 비율 |
| Stuck job count | `summary_job_stuck_total` | **0 유지** | RUNNING 상태로 비정상 장시간 정체된 job 탐지 |
| Failed job | 최근 5분 failed 발생 수 | **0 목표** | 개별 종목 요약 실패를 빠르게 인지하기 위한 운영 기준 |
| End-to-end latency | `summary_job_total_elapsed_seconds{stat="p95"}` | **p95 < 15s** | SummaryJob 생성부터 완료까지의 처리시간 |

### 4. Freshness

| 항목 | SLI | SLO | 비고 |
|---|---|---|---|
| Summary freshness | SummaryJob 생성 후 summary가 준비되기까지 걸린 시간 | **생성 후 15분 내 95% 완료 목표** | 배치 완료 품질과 사용자 관점의 신선도 관리용. 현재는 초안 단계이며 추후 보강 예정 |

### 5. Dependency / Infra

| 항목 | SLI | SLO | 비고 |
|---|---|---|---|
| Readiness | DB / Redis / Finnhub readiness 상태 | **정상 유지** | 인프라 및 외부 의존성 장애 감지 |
| Host health | CPU / Memory / Disk / Network 상태 | **임계치 초과 없음** | 인프라 원인 분석용 보조 지표 |

### 운영 원칙

- **SLI**는 실제로 측정하는 운영 지표입니다.
- **SLO**는 해당 지표에 대해 내부적으로 목표로 삼는 값입니다.
- 현재 StockQ는 개인 프로젝트 단계이므로, 외부 고객과의 계약 수준인 **SLA**까지 두기보다는 **SLI / SLO** 중심으로 운영 기준을 정의했습니다.
- 알림은 모든 지표에 일괄적으로 거는 것이 아니라, 실제 대응 가치가 높은 항목부터 우선 적용했습니다.
  - `summary_job_stuck_total > 0`
  - 최근 5분 `failed SummaryJob > 0`

### 현재 상태

- Prometheus / Grafana 기반 메트릭 수집 및 시각화 구성 완료
- SummaryJob 관련 커스텀 메트릭 구현 완료
- `GET /api/stocks/summaries/` 전용 route 기반 커스텀 API 메트릭 추가 완료
- `GET /api/stocks/search/` 전용 route 기반 커스텀 API 메트릭 추가 완료
- Slack 알림 연동 완료
- 현재 적용 중인 대표 알림
  - `summary_job_stuck_total > 0`
  - 최근 5분 `failed SummaryJob > 0`
- 현재 확인 가능한 대표 API 지표
  - Read API p95 latency
  - Read API request rate
  - Read API error rate
  - Read API successful requests (Last 5m)
  - Search API p95 latency
  - Search API request rate
  - Search API error rate
  - Search API successful requests (Last 5m)

### 앞으로의 보강 예정

- favorites, auth 등 다른 핵심 API에도 route 기준 커스텀 API 메트릭 확장
- freshness SLI를 실제 운영 지표로 정교화
- 알림 레벨(info / warning / critical) 체계화
- SLO 달성률을 주간/월간 단위로 점검하는 방식으로 확장

## 한 줄 정리

StockQ는 단순한 뉴스 요약 기능 구현을 넘어서,  
LLM 생성 작업을 요청 경로에서 분리하고, 병목을 실측해 queue 적체·직렬화·N+1 문제를 개선했으며, summaries/search 핵심 read API에 대한 route 기준 메트릭과 운영 알림까지 갖춘 비동기 파이프라인 프로젝트입니다.