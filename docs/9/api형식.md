StockQ — 5일 완주 플랜 (가능하면 5일, 최대 8일 백업)

타임존: Asia/Seoul · 시작: 2025-09-18(목)

Day 1 — 9/18(목): 백엔드 필수 차단 제거 [BLOCKER DAY]

목표: 서버가 “출시 가능 최소 조건”을 만족하도록 상태/인증/스케줄 뼈대 완성.

 /api/auth/logout (JWT Blacklist)

rest_framework_simplejwt.token_blacklist 활성, migrate

POST /api/auth/logout {refresh} → 204

 /api/readiness

DB/Redis ping + 외부 API(Finnhub) 토큰 검증(타임아웃 500–800ms)

 /api/schema (drf-spectacular) + Swagger/Redoc 노출

 News.external_id UNIQUE 마이그레이션 (중복 방지)

 Celery Beat 스케줄 등록

06:00 KST fetch_news(favorites, window=1d)

06:30 KST generate_summary(user, date=today, per-symbol)

 레이트리밋 안전 화

배치 50건/분, 429 지수백오프(2→4→8…≤64s, max_retries=5)

산출물/검증

curl 스크린샷: /health, /readiness, /schema 200

Celery 로그: Beat 스케줄 로드 확인

마이그레이션 로그 + UNIQUE 제약 확인

Day 2 — 9/19(금): API 완성 & E2E(앱 없이) 점검

목표: 가격/요약/태스크 API 마무리, 서버만으로 E2E 성립.

 Prices API

GET /api/stocks/{symbol}/prices?start&end&interval

내부 fetch_prices bulk upsert

 요약 파이프라인(rule 기반)

POST /api/summaries {date,symbol?} → 202 {task_id}

GET /api/summaries?date&symbol? / GET /api/tasks/{id}

 /api/ingest 태스크화(비동기) + 상태 조회

 즐겨찾기→인제스트→요약→피드 API 시나리오 테스트 스크립트 작성

산출물/검증

관리 커맨드/HTTP 호출 순서대로 로그 캡처

단위 테스트(핵심 happy-path 8~12개) 통과 스샷

Day 3 — 9/20(토): RN 앱 골격 + 인증 흐름(이메일/비번 + Google)

목표: 앱에서 로그인/로그아웃·토큰보관·즐겨찾기 CRUD 구동.

 RN 프로젝트(Expo) 부트스트랩, env 분리

 보안 저장: expo-secure-store에 access/refresh 저장 [BLOCKER]

 토큰 로테이션: 401 시 refresh→재시도 1회→로그아웃

 이메일/비번 로그인 화면 + 흐름

 Google OAuth (서버 검증형)

RN: AuthSession로 id_token/authorization_code 획득

서버: JWK 검증·aud 매칭 후 JWT 발급

iOS 회피전략: iOS는 당장 이메일 로그인만 노출(3rd party 노출 시 Apple Sign-in 의무)

5일 플랜: Android에만 Google 노출 / iOS는 이메일만

 즐겨찾기 화면: 검색/추가/삭제, 리스트

산출물/검증

RN 화면 데모(영상/스크린샷): 로그인→즐겨찾기 추가/삭제 성공

서버 로그: /auth/logout 정상 작동

Day 4 — 9/21(일): “오늘 리포트” 화면 + 뉴스 피드 + 간이 푸시

목표: 사용자가 아침에 앱을 열면 요약 카드와 기사 리스트를 소비 가능.

 리포트 탭: GET /api/summaries?date=today 카드 리스트

 뉴스 피드: 즐겨찾기 기반 최신 기사 카드(원문 열기)

 당일 알림 MVP

간이 푸시: Expo Notifications(안드), iOS는 로컬 알림로 대체 가능

백엔드: 06:40 KST에 “리포트 준비” 토픽/유저별 트리거(간이 버전이면 폴링)

 스켈레톤/오프라인 처리/에러 토스트

산출물/검증

06:30 요약 태스크 완료 로그 + 06:40 알림/폴링으로 리스트 노출 영상

API 실패/오프라인 UX 캡처

Day 5 — 9/22(월): 문서/헬스·스키마 최종화 + 베이스라인 측정 + 기초 최적화

목표: 릴리스 전 체크리스트를 닫고 P95 베이스라인 확보.

 /schema·/health·/readiness 페이지 캡처 (릴리스 산출물)

 Locust 베이스라인(50 RPS · 5분)

측정값: 피드/요약 조회 P50/P95, 에러율, 쿼리 수

 1차 최적화(가벼운 것만)

인덱스 점검 + select_related/prefetch_related

GZip + JSONRenderer

 README/LAUNCH_MVP_SPEC 갱신(화면샷·엔드포인트·측정표 포함)

산출물/검증

리포트(P50/P95 그래프) · 전/후 개선치 표

릴리스 태그 v1.0.0-rc1

8일 백업 플랜(3일 추가: 9/23~9/25)
Day 6 — 9/23(화): Apple/Kakao OAuth + iOS 정합

 Sign in with Apple (서버 JWK 검증, nonce) [iOS 심사 필수]

 Kakao(선택): access_token → /v2/user/me

 RN: iOS에 Apple 버튼 노출, Google은 iOS에도 활성

Day 7 — 9/24(수): 캐싱·태스크 튜닝·가격 뷰 보강

 유저/페이지 캐싱(TTL 60s) + ETag/If-None-Match

 Celery concurrency/batch 사이즈 튜닝, 429 대응 로그

 Prices 조회 루트 UX 개선(기간 프리셋, 무한스크롤/페이지)

Day 8 — 9/25(목): CI/CD·스토어 패키징·정책 문서·최종 리허설

 GitHub Actions: test/lint/build/docker push

 Privacy/ToS 페이지 공개(URL 앱 내 링크)

 스토어 스크린샷/설명/아이콘 준비(투자권유 문구 금지)

 풀 리허설: 06:00→06:30→06:40 흐름 녹화

병렬/리스크 메모

Apple 의무조건: iOS에서 타사 로그인(구글/카카오) 쓰면 Sign in with Apple 필수. 5일 플랜에서는 iOS에 타사 로그인 미노출로 회피, 8일 플랜에서 Apple 넣어 정식 대응.

Rate Limit: 실제 즐겨찾기 수가 크면 배치 크기 50, 슬립 60s, 실패 재큐 추천.

푸시 대체: 심사까지 급하면 “앱 내 아침 배지/배너 + 폴링(06:40 이후 1회)”로 대체 가능.

오늘 바로 착수 체크리스트 (9/18)

 blacklist 앱 추가·migrate, /auth/logout 구현

 /readiness(DB/Redis/Finnhub) 구현

 drf-spectacular 설치·/schema 노출

 Beat 스케줄(06:00/06:30) 등록, 로그 확인

 external_id UNIQUE 생성

원하면 위 순서대로 커밋 단위, 테스트 포인트, **예상 소요(시간)**까지 더 잘게 쪼개서 이슈 템플릿으로 만들어주겠다.