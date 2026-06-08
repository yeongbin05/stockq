## 소개

원하는 주식을 즐겨찾기에 등록하면 매일 아침 종목별 요약을 받아볼 수 있는 서비스입니다.

## 기술 스택

| 구분 | 기술 |
|---|---|
| Backend | Python, Django, Django REST Framework, Celery |
| Database/Cache | PostgreSQL,Redis |
| Infra / Deploy | Docker, AWS EC2, GitHub Actions |
| Monitoring | Prometheus, Grafana |


## 아키텍처

![StockQ Architecture](docs/images/architecture.png)


<!-- ## ERD

![StockQ ERD](docs/images/db.png) -->

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
Queue에 넣는 속도와 Worker가 처리하는 속도 사이의 균형이 맞지 않은 것이 문제였습니다.

**해결**
Dispatcher가 현재 실행 중인 Job 수를 확인하고 Worker가 처리 가능한 슬롯만큼만 새 작업을 내보내도록 변경했습니다.

**결과**
Queue 대기 시간이 **최대 9.3초 → 0.03~0.24초**로 안정화됐습니다.

### 2. 직렬화 병목 발견 및 N+1 쿼리 제거

**문제**
주식 검색 API의 응답이 약 1.7초에 달했습니다. 처음엔 DB 쿼리가 느린 것으로 예상했습니다.

**원인 파악**
Django Debug Toolbar로 확인한 결과 SQL 실행 자체는 약 6ms로 짧았지만 객체마다 추가 쿼리를 발생시키는 N+1 문제가 있었습니다. 또한 브라우저 Network 탭에서 응답 크기가 큰 것을 확인했고 페이지네이션 없이 많은 객체를 한 번에 반환하면서 DRF 직렬화 비용과 전송 데이터 크기가 커지고 있었습니다.

**해결**
- 페이지네이션(`CursorPagination`) 도입으로 한 번에 반환하는 객체 수를 제한
- 즐겨찾기 여부와 최신 가격을 객체마다 따로 조회하던 구조를 `Exists`, `Subquery` annotation으로 변경해 N+1 쿼리를 제거

**결과**
검색 API 응답 시간을 **1,750ms → 37ms**, 응답 크기를 **2.2MB → 약 30KB**, 쿼리 수를 **1001개 → 1개**로 개선했습니다.


### 3. 외부 API Rate Limit 대응

**문제**
뉴스 수집 과정에서 여러 종목의 외부 API(Finnhub) 호출이 짧은 시간에 몰리면서 429 에러가 발생했습니다.

**원인 파악**
호출 간격과 전체 호출량을 중앙에서 제어하는 장치가 부족했습니다.

**해결**
Redis + Lua 기반 token bucket을 적용해 외부 API 호출 전에 호출 가능 여부를 확인하도록 했습니다. 허용량이 부족한 경우 즉시 API를 호출하지 않고 작업 특성에 따라 대기하거나 재시도 흐름으로 넘기도록 처리했습니다.

### 4. Worker 장애 시 Stuck Job 자동 복구

**문제**
Worker가 죽거나 네트워크 타임아웃이 발생하면 처리 중이던 Job이 `RUNNING` 상태로 계속 남아 다시 처리되지 않는 문제가 있었습니다.

**해결**
Job에 마지막 상태 갱신 시각을 기록해두고 일정 시간 이상 갱신되지 않은 Job을 Stuck으로 판단해 자동 재처리하도록 했습니다. 이때 오래된 Worker가 뒤늦게 결과를 덮어쓰는 문제를 막기 위해 각 처리 시도마다 고유 토큰(`lease_token`)을 발급해 유효한 Worker의 결과만 반영되도록 했습니다.
