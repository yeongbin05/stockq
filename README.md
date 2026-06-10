## 소개

원하는 주식을 즐겨찾기에 등록하면 매일 아침 종목별 요약을 받아볼 수 있는 서비스입니다.

## 기술 스택

| 구분 | 기술 |
|---|---|
| Backend | Python, Django, DRF, Celery |
| Database | PostgreSQL, Redis |
| Infra | Docker, Nginx, AWS EC2|
| Monitoring | Prometheus, Grafana |


## 아키텍처

![StockQ Architecture](docs/images/architecture.png)


## ERD

![StockQ ERD](docs/images/db.png)

## 화면

<img src="docs/images/summary.jpg" width="240" height="500" />
<img src="docs/images/search.jpg" width="240" height="500" />
<img src="docs/images/news.jpg" width="240" height="500" />
<img src="docs/images/profile.jpg" width="240" height="500" />


## 트러블슈팅 & 성능 최적화

### 1. Queue 적체 해결 — Backpressure 적용

**문제**
LLM 요약 작업을 백그라운드로 분리하기 위해 Celery를 도입했지만 생성된 Job을 한꺼번에 Queue에 넣다 보니 Worker가 처리할 수 있는 양을 초과해 대기 시간이 폭증했습니다.

**원인 파악**
Queue에 넣는 속도와 Worker가 소화하는 속도 사이의 균형이 없었던 것이 문제였습니다.

**해결**
Dispatcher가 Queue에 넘겼지만 아직 완료되지 않은 Job 수를 확인하고 빈 슬롯만큼만 새 작업을 내보내도록 변경했습니다. 생산자가 소비자의 처리 속도에 맞춰 속도를 조절하는 방식(Backpressure)입니다.

**결과**
Queue 대기 시간이 **최대 9.3초 → 0.03~0.24초**로 안정화됐습니다.

### 2. 직렬화 병목 발견 및 N+1 쿼리 제거

**문제**
주식 검색 API의 응답이 약 1.7초에 달했습니다. 처음엔 DB 쿼리가 느린 것으로 예상했습니다.

**원인 파악**
Django Debug Toolbar로 확인한 결과 SQL 실행 자체는 약 6ms로 짧았지만 객체마다 추가 쿼리를 발생시키는 N+1 문제가 있었습니다. 또한 페이지네이션 없이 많은 객체를 한 번에 반환하면서 DRF 직렬화 비용과 응답 데이터 크기가 커진 것이 병목으로 판단됐습니다.

**해결**
- 페이지네이션(`CursorPagination`) 도입으로 한 번에 반환하는 객체 수를 제한
- 즐겨찾기 여부와 최신 가격을 객체마다 따로 조회하던 구조를 `Exists`, `Subquery` annotation으로 변경해 N+1 쿼리를 제거

**결과**
검색 API 응답 시간을 **1750ms → 37ms**, 쿼리 수를 **1001개 → 1**개로 개선했습니다.


### 3. 외부 API Rate Limit 대응 — Redis Token Bucket 기반 호출 제어

**문제**
뉴스 수집 과정에서 여러 종목의 Finnhub API 호출이 짧은 시간에 몰리면서 429 에러가 발생했습니다. 

**원인 파악**
기존 뉴스 수집 로직은 관심 종목을 순회하며 종목별로 Finnhub API를 호출하는 구조였습니다. 호출 전 전체 요청량을 제어하는 장치가 없어 종목 수가 늘어날수록 짧은 시간 안에 요청이 집중되었고 Finnhub rate limit에 도달할 수 있었습니다.

**해결**
Finnhub의 요청 제한을 기준으로 Redis 기반 token bucket을 적용해 호출량을 조절했습니다. API 호출 전에 요청 가능한 slot이 있는지 확인하고 slot이 있을 때만 Finnhub를 호출하도록 변경했습니다. 그래도 429 응답이 발생하면 exponential backoff와 jitter를 적용해 재시도했습니다.

OpenAI 요약 호출에도 같은 요청 slot 제어를 적용해 slot이 부족할 때는 API를 호출하지 않고 SummaryJob을 RETRY_WAIT 상태로 돌려 나중에 다시 처리하도록 했습니다.

**결과**
Finnhub API 호출이 한 번에 몰리는 상황을 줄이고 429 응답이 발생해도 재시도 흐름으로 복구할 수 있게 했습니다.

### 4. Worker 장애 시 Stuck Job 자동 복구

**문제**
비동기 작업 처리 중 예기치 않은 오류나 작업 중단으로 인해 RUNNING 상태의 Job이 완료되지 못하고 남을 수 있습니다.

**해결**
일정 시간 동안 정상적으로 처리 완료되지 않은 Job을 Stuck Job으로 판단해 다시 처리할 수 있도록 했습니다. 또한 각 처리 시도마다 고유한 lease_token을 발급해 오래된 Worker가 뒤늦게 결과를 저장하더라도 현재 유효한 처리 시도의 결과만 반영되도록 했습니다.