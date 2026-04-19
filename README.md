# Performance & Reliability

## 핵심 요약

- LLM 요약 생성을 요청 경로에서 분리하고, **SummaryJob + Celery 기반 비동기 파이프라인**으로 재구성했습니다.
- 실측 결과, 주요 병목은 DB가 아니라 **LLM 요약 단계**였으며 **total p95 8.16s**, **llm p95 6.75s**, **relevance p95 1.92s**를 확인했습니다.
- 기존 burst dispatch로 발생하던 worker queue 적체를 **inflight-slot 기반 dispatch**로 개선해 queue wait를 크게 줄였습니다.
- 실제 배포 환경에서 `GET /api/stocks/summaries/`는 **20 VU에서 p95 187.3ms, 에러율 0%**로 안정적으로 동작했습니다.
- Redis Lua 기반 rate limiting, 상태 기반 SummaryJob 관리, 중복 방지, production 검증을 통해 운영 안정성을 강화했습니다.

---

## 1. 요청 경로와 생성 파이프라인 분리

주식 뉴스 요약을 사용자 요청 시점마다 LLM으로 생성하면 응답 시간이 길어지고 read API의 안정성이 흔들릴 수 있습니다.  
이를 해결하기 위해 다음과 같은 비동기 파이프라인으로 재구성했습니다.

**Finnhub API → PostgreSQL 저장 → SummaryJob 생성 → Celery worker 요약 생성 → Summary 저장 → Read API 조회**

이 구조를 통해 비용이 큰 생성 작업은 백그라운드에서 처리하고, 사용자 요청 시에는 저장된 결과만 조회하도록 분리했습니다.

---

## 2. 요약 파이프라인 병목 실측

파이프라인에 stage-level timing을 추가해 실제 병목 구간을 측정했습니다.

- **total p95: 8.16s**
- **llm p95: 6.75s**
- **relevance p95: 1.92s**

또한 `AAPL`, `NVDA`, `TSLA`, `MSFT` 기준 벤치마크에서 뉴스 수집은 대체로 **~1초 내외**, 평균 LLM 요약 시간은 **~8.46초**였습니다.

즉, 주요 병목은 DB가 아니라 **LLM 요약 단계**였고, 이 결과를 바탕으로 최적화 우선순위를 DB보다 **LLM 비용, latency, 입력량 제어**에 두었습니다.

---

## 3. Inflight-Slot 기반 Dispatch로 Queue Wait 개선

기존에는 pending 잡을 한 번에 enqueue하는 **burst dispatch** 방식 때문에 worker queue 적체가 발생했습니다.  
실제 관찰에서는 **queue_wait p95 37.48s**가 발생했고, 한 번에 **17개 잡**이 디스패치되며 queueing이 발생했습니다.

`worker concurrency=2` 환경에서 SummaryJob 5개를 재현했을 때:

### 기존 burst dispatch
- `0.101s`
- `0.122s`
- `4.922s`
- `5.286s`
- `9.289s`

### 개선 후 inflight-slot dispatch
- `0.235s`
- `0.238s`
- `0.157s`
- `0.165s`
- `0.031s`

즉, 실제 worker capacity에 맞춰 작업만 dispatch하도록 바꾸어 **admission control / backpressure**를 적용했고, 그 결과 queue 적체를 줄이며 운영 안정성을 높였습니다.

---

## 4. Precomputed Summary 기반 Fast Read Path

LLM 요약 생성은 평균 **~8초 이상**이 걸리기 때문에, 이를 요청 시점에 직접 수행하면 read API latency가 쉽게 악화됩니다.  
이를 방지하기 위해 요약은 Celery 배치에서 미리 생성하고, API는 저장된 결과만 조회하도록 구성했습니다.

배포 환경에서 `GET /api/stocks/summaries/`를 k6로 테스트한 결과:

- **3 VU, 30초**: `p95 87.52ms`, `p99 286.99ms`, `에러율 0%`
- **10 VU, 30초**: `p95 49.15ms`, `p99 446.83ms`, `에러율 0%`, `약 34.4 RPS`
- **20 VU, 30초**: `p95 187.3ms`, `p99 242.33ms`, `에러율 0%`, `약 53.9 RPS`

테스트 당시 DB에는 실제 요약 데이터 **59건**이 존재했고, 측정 결과 read API는 **20 VU까지 안정적으로 동작**했습니다.  
이를 통해 현재 주요 병목은 read API가 아니라 **Celery 기반 요약 파이프라인** 쪽임을 확인했습니다.

---

## 5. 외부 API 안정성과 입력 최적화

외부 API 제한으로 인한 배치 불안정을 줄이기 위해 다음을 적용했습니다.

- Redis + Lua token bucket 기반 rate limiting
- Finnhub 호출 전 `wait_for_slot()` 적용
- `429 Too Many Requests` 및 네트워크 오류에 대한 exponential backoff retry

또한 LLM 입력 최적화를 위해 relevance filtering을 적용해 관련 기사만 선별했습니다.

- `raw_count = 10`
- `relevant_count = 5`
- token `400 → 297` (**약 25% 감소**)

이를 통해 외부 API 호출 안정성을 높이고, **LLM 비용 효율과 요약 품질**을 함께 개선했습니다.

---

## 6. Production Validation

SummaryJob 기반 파이프라인은 production 환경에서 end-to-end로 검증했습니다.

- 동일 `(stock, date)` 중복 생성 방지
- `success`, `failed`, `no_relevant_news` 등 상태 기반 추적
- token `400 → 297` 최적화 확인
- stuck job 미발생 확인

StockQ는 단순히 뉴스를 요약하는 기능 구현을 넘어서, **LLM 생성 비용 분리, 병목 실측, queue 적체 완화, 외부 API 제어, production 검증**까지 포함해 실제 운영 가능한 구조를 만드는 데 집중한 프로젝트입니다.