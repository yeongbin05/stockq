# StockQ 14-Day MVP — 기능 우선 → 성능 개선 증빙 플랜 (2025-09-09 \~ 2025-09-22, KST)

> **의도**: “기능 먼저 완성 → 측정 → 단계적 최적화로 전/후 개선 수치를 증빙”을 노린다. 신입 상위권 포지셔닝을 위해 **수치화된 전/후 비교**와 \*\*운영 관점(관측성·CI/CD·배포)\*\*까지 포함한다.

---

## 0) 원칙

* **기능 우선**: 1주차에 핵심 기능 전부 동작. 의도적으로 캐시/고급 최적화 **미적용** 상태로 **베이스라인** 계측.
* **측정 주도**: 모든 최적화는 **P95 응답, QPS, CPU/메모리, DB 쿼리 수**로 효과를 기록.
* **점진 최적화**: 2주차에 인덱스/쿼리/캐시/배치/워커/웹서버 순으로 적용, **각 단계별 개선 %** 명시.
* **실무 기준**: Postgres/Redis/Celery/DRF, Docker, GitHub Actions, Swagger(스키마) 필수.

---

## 1) Executive Summary

* **목표**: 관심종목 기반 **미국 주식 뉴스 수집→중복제거→규칙 요약→피드 제공**을 API로 완성하고 배포. 2주차에 성능 개선으로 **개선 수치** 확보.
* **SLO(1차)**: P95 < **300ms**(피드/즐겨찾기), 오류율 < **0.5%**. 2주차 튜닝 후 **P95 ≤ 200ms** 목표.
* **완료 기준(DoD)**

  * Docker Compose로 **web/pg/redis/celery(worker/beat)** 1명령 기동
  * `/api/health/`(공개), `/api/readiness/`(DB/Redis/Celery) 응답 OK
  * 인증, 즐겨찾기, 뉴스 인제스트/요약(규칙), 피드, 가격 API 동작
  * Swagger `/api/schema/` + Swagger UI/Redoc 노출
  * Locust 리포트(베이스라인, 최적화 후)와 **전/후% 개선표**
  * GitHub Actions CI(테스트·린트·도커 빌드) + 배포 스크립트
* **2주 내 완성 확률**: **80%** (외부 API rate limit/PC 환경변수 이슈가 리스크)
* **레벨 판단**: 본 플랜 달성 시 **신입 상위\~1-2년차 체감**. 전/후 성능 증빙 + CI/CD + 스키마 제공까지 제출하면 **상위 신입(네카라쿠배) 어필 가능**.

---

## 2) 스코프 & 논골(Non-Goals)

* **스코프**: REST API 백엔드 + 간이 템플릿 UI(데모용). 푸시 알림/실시간 WS는 제외.
* **논골**: 자동매매/차트 고급 기능/다국가 확장/실시간 푸시(FCM, APNs).

---

## 3) 데이터 모델 (핵심 요약)

* **Stock**: `symbol UNIQUE`, `name`, `exchange`, `currency`
* **FavoriteStock**: `user↔stock UNIQUE(user, stock)`
* **News**: `stock_id`, `title`, `url`, `source`, `published_at`, `external_id UNIQUE`, `content`
* **Summary**: `user_id`, `date`, `stock_id?`, `summary`, **UNIQUE(user,date,stock\_id NULLS DISTINCT)**
* **Price**: `stock_id`, `ts`, `ohlcv`, **UNIQUE(stock, ts)**
* 인덱스: `(stock_id,published_at DESC)`, `(stock_id,ts DESC)`, `external_id UNIQUE`

---

## 4) API 컨벤션

* **Base**: `/api`
* **Auth**: JWT(Access/Refresh). 기본 권한 `IsAuthenticated`, 공개만 `AllowAny`.
* **Throttle**: `Anon=30/min`, `User=120/min` (로그인/회원가입은 더 보수)
* **CORS**: dev=\* 허용, prod=화이트리스트만.
* **헬스**: `/api/health/`(공개) / `/api/readiness/`(의존성 점검)
* **스키마**: `/api/schema/` + Swagger UI/Redoc
* **에러 포맷(예)**

```json
{"error":{"code":"duplicate","message":"favorite already exists","detail":{"symbol":"AAPL"}}}
```

---

## 5) 엔드포인트 명세 (발췌)

### Auth

* **POST /api/auth/signup** `{email,password,nickname?}` → `201 {id,email}`
* **POST /api/auth/login** `{email,password}` → `200 {access,refresh}`
* **POST /api/auth/refresh** `{refresh}` → `200 {access}`
* **POST /api/auth/logout** `{refresh}` → `204`(블랙리스트)

### Stocks & Favorites

* **GET /api/stocks?q\&limit** → `[ {id,symbol,name,exchange,currency} ]`
* **GET /api/stocks/{symbol}** → `{...}`
* **GET /api/favorites** → `[ {stock:{symbol,name}, created_at} ]`
* **POST /api/favorites** `{symbol}` → `201 {...}` / **409**(중복) / **400**(미존재)
* **DELETE /api/favorites/{symbol}** → `204`

### News & Summary

* **GET /api/news?symbol?\&since?\&page\&limit(≤50)** → 최신순 목록(즐겨찾기 기본)
* **POST /api/summaries** `{date?,symbol?}` → `202 {task_id}` (LLM은 feature flag)
* **GET /api/summaries?date\&symbol?** → 요약 또는 `404`
* **GET /api/tasks/{task\_id}** → 태스크 상태

### Prices

* **GET /api/stocks/{symbol}/prices?start\&end\&interval=1m|5m|1d** → OHLCV 배열

### System

* **GET /api/health/** 200 OK
* **GET /api/readiness/** DB/Redis/Celery ping
* **GET /api/schema/** Swagger/Redoc

---

## 6) 비동기 태스크(Celery)

* `fetch_news(symbol:str, limit:int=50)` → 외부 API → `external_id` 기반 **upsert**(재시도/백오프/타임아웃)
* `fetch_prices(symbol, start,end,interval)` → (stock,ts) **bulk upsert**
* `generate_summary(user_id, date, symbol?)` → **규칙 요약** 기본, `SUMMARY_USE_LLM=true`면 LLM 사용
* Beat: **매일 09:00 KST** 수집/요약 배치. 개발 중에는 10분 주기로 스케줄 테스트.

---

## 7) 14일 상세 플랜 — “기능 먼저, 그 다음 성능”

> 날짜는 KST. 각 일자에 **산출물**과 **측정/목표**를 포함.

### Day 1 (9/9) — 환경/설정/CI

* settings: `base/local/prod.py`, `.env.example`, `TIME_ZONE='Asia/Seoul'`, `USE_TZ=True`
* Docker Compose: `postgres, redis`
* CI: GitHub Actions 워크플로우(테스트·린트·이미지 빌드)
* Swagger: drf-spectacular 설치, `/api/schema/` 노출
* **산출물**: compose, actions yaml, 스키마 엔드포인트 스샷

### Day 2 (9/10) — 모델 & 즐겨찾기 API(+테스트)

* 모델 정의/마이그레이션(Stock/Favorite/News/Summary/Price)
* Favorites API(POST/DELETE/GET), 409/400 처리
* pytest 기본 + factory\_boy, 커버리지 시작
* **산출물**: 테스트 통과, Admin 등록(검색/필터)

### Day 3 (9/11) — 뉴스 인제스트 v1(단순)

* 관리커맨드 `manage.py fetch_news AAPL --limit 50`
* News upsert(UNIQUE external\_id), URL 정규화(utm 제거, NFKC)
* Beat 스케줄 **09:00**(dev: 10분)
* **산출물**: 인제스트 로그, 최초 데이터 적재

### Day 4 (9/12) — 피드 API v1(최적화 없음)

* **GET /api/news**(즐겨찾기 기반), 페이지네이션
* 고의로 **캐시/선행최적화 미적용** (베이스라인 확보 목적)
* **측정(베이스라인)**: Locust 5분, 50 RPS → 예상 P95 **400\~600ms**
* **산출물**: 리포트(요청 수/에러율/P95), 쿼리수(Log) 캡처

### Day 5 (9/13) — 가격 API v1(최적화 없음)

* Prices 인제스트/조회 API(범위 필터)
* **측정(베이스라인)**: 기간 30일 조회 P95 **350\~700ms** 예상
* **산출물**: 리포트, 쿼리 계획(EXPLAIN) 캡처

### Day 6 (9/14) — 인증/보안 기본

* signup/login/refresh/logout(블랙리스트)
* 전역 `IsAuthenticated`, 공개만 `AllowAny`
* Throttle 수치 적용(Anon 30/min, User 120/min)
* `/api/health/`, `/api/readiness/` 구현
* **산출물**: 보안 체크리스트, 헬스 스샷

### Day 7 (9/15) — 규칙 요약 & E2E

* 규칙 요약(첫 N문장/리드 문장 추출/괄호·중복 공백 정리)
* `POST /api/summaries` → Celery 태스크, 상태 조회
* E2E: 로그인→즐겨찾기→인제스트→피드→요약
* **산출물**: E2E 리포트, 베타 태그 `v0.1.0`(기능 완료)

> **이 시점**: 기능 완료 + 베이스라인 성능 자료 확보.

### Day 8 (9/16) — DB 인덱스/쿼리 정리(1차 최적화)

* 인덱스: `(stock_id,published_at DESC)`, `(stock_id,ts DESC)` 확인/추가
* 쿼리: 피드 `select_related(stock)` 적용, 필요 필드만 선택
* **목표 개선**: 피드 P95 **-30%** (예: 500→350ms)
* **산출물**: EXPLAIN 전/후, 리포트(개선 %)

### Day 9 (9/17) — N+1 제거/직렬화/압축

* prefetch/select\_related 전수 점검(즐겨찾기→피드 경로)
* DRF Serializer 최적화(중첩 최소화, values 쿼리 검토)
* **GZip 압축** 활성, JSONRenderer만 사용
* **목표 개선**: 피드 추가 **-15%p** (누적 -40\~45%)
* **산출물**: 전/후 리포트

### Day 10 (9/18) — 캐싱 & 검증 헤더

* 사용자별/페이지별 캐시 키 `news:{uid}:{symbol?}:{page}` TTL 60s
* `ETag/If-None-Match` 적용(최신 `updated_at` 기반)
* **목표 개선**: **캐시 히트 시 P95 -60\~80%** → **100\~180ms** 구간
* **산출물**: 히트율·지연 그래프, 캐시 키 설계 문서

### Day 11 (9/19) — 인제스트 처리량 증대

* Prices/News **bulk upsert** 배치 크기 튜닝(500\~2000)
* Celery concurrency/큐 설정, 재시도 백오프, 데드레터(옵션)
* **목표 개선**: 인제스트 처리량 **3\~5배↑**, 실패율 <0.5%
* **산출물**: 배치 전/후 처리 rps 표

### Day 12 (9/20) — 부하테스트(본선) & 웹서버 튜닝

* Locust 300 RPS 1시간, P50/P95/P99 수집
* Gunicorn/uvicorn workers, keep-alive, DB 커넥션 풀 사이즈 조정
* **목표**: 피드 P95 **≤ 200ms**, 오류율 <0.5%
* **산출물**: 리포트 그래프, 튜닝 파라미터 표

### Day 13 (9/21) — 배포/CI 마무리

* Dockerfile 멀티스테이지, prod compose, 마이그레이션 훅
* GitHub Actions: 테스트·린트·도커 빌드·이미지 푸시(레지스트리)
* 스테이징/프로덕션 매니페스트 분리
* **산출물**: 배포 로그, `/api/health/` 캡처(운영)

### Day 14 (9/22) — 최종 리포트 & 릴리스

* “기능→측정→최적화” **전/후 비교 리포트** 완성
* README(실행법/ENV/ERD/API/성능 그래프/EXPLAIN) 업데이트
* 릴리스 태그 `v1.0.0-rc1`
* **산출물**: 최종 PDF/이미지 그래프, 체크리스트 전부 체크

---

## 8) 성능 개선 목표표(샘플 수치 가이드)

| 경로             |  베이스라인 P95 |                      단계별 목표 |                   최종 목표 |
| -------------- | ---------: | --------------------------: | ----------------------: |
| **뉴스 피드 GET**  | 400\~600ms | 인덱스·쿼리: -30% → ~~300~~420ms |   캐시 히트: **100\~180ms** |
| **가격 30일 GET** | 350\~700ms | 인덱스·쿼리: -40% → ~~210~~420ms | 캐시/압축 후: **150\~250ms** |
| **인제스트 처리량**   |         1× |          bulk+워커: **3\~5×** |            에러율 <0.5% 유지 |

> 실제 수치는 리포트에 **전/후 그래프**로 고정 자산화한다(README/Evidence).

---

## 9) 보안/운영

* JWT: Access 15m, Refresh 14d(예), 로그아웃=블랙리스트
* 입력 검증: symbol 화이트리스트(DB 존재 심볼만 통과), 페이지 상한(≤50)
* XSS: 요약/콘텐츠는 HTML escape
* 로깅: JSON 구조화( trace\_id, user\_id, path, status, latency\_ms )
* 메트릭: 요청 지연 히스토그램, 큐 길이, 태스크 실패율
* 알람(선택): 오류율/큐 적체/P95 임계 경보
* redis lua로 봇 감지 등 user별로 api요청 횟수 제한 걸기
---

## 10) Evidence(제출물) 체크리스트

* [ ] 배포 URL + `/api/health/` 캡처
* [ ] Swagger/Redoc `/api/schema/` 스샷
* [ ] 테스트 15\~20개 + 커버리지 ≥60% 스샷
* [ ] `manage.py fetch_news` 실행 로그/중복 제거 규칙 문서(정규화·해시)
* [ ] 핵심 쿼리 EXPLAIN 전/후 1\~2개
* [ ] Locust 리포트(베이스라인 & 최적화 후) + **개선% 표**
* [ ] Dockerfile/compose, GitHub Actions 실행 로그

---

## 11) 이력서 어필 문구(예시)

* “관심종목 뉴스 수집·요약 백엔드를 **2주** 내 MVP→배포. 기능 완성 후 **측정 기반 최적화**로 뉴스 피드 **P95 520ms→160ms(△69%)**, 가격 조회 **620ms→210ms(△66%)**, 인제스트 처리량 **×4.2** 개선. Swagger/CI/CD/관측성까지 포함하여 **운영 수준** 품질 달성.”

---

## 12) ENV(요약)

* `DJANGO_SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`, `REDIS_URL`, `FINNHUB_API_KEY`, `OPENAI_API_KEY`, `DJANGO_SETTINGS_MODULE=stockq.settings.prod`, `SUMMARY_USE_LLM=false`

---

## 13) 부록 — 규칙 요약 로직(초안)

1. 본문에서 마크업/URL 제거 → 문장 분할
2. 제목 포함 키워드가 있는 첫 2~~3문장 우선(길이 캡 200~~260자)
3. 괄호·중복 공백 정리, 고유명사 대문자 보정
4. 실패 시 첫 문장 폴백

---

**메모**: “기능 먼저 → 의도적 미최적화 → 측정 → 단계별 최적화(인덱스→쿼리→캐시→배치→워커→웹서버)” 순서를 반드시 지켜 **개선 퍼센트**를 증빙 자료로 남긴다.
