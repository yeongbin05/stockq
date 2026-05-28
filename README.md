## 소개

원하는 주식을 즐겨찾기에 등록하면 매일 아침 종목별 요약을 받아볼 수 있는 서비스입니다.

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

## 아키텍처

![StockQ Architecture](docs/images/architecture.png)


# ERD

![StockQ ERD](docs/images/db.png)

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


