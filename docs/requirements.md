# StockQ Requirements

## 🎯 MVP (v0.1)

### 핵심 유저 가치
- 내가 고른 종목의 **미국 주식 뉴스**를 **간단 요약**으로 **매일 빠르게** 확인

### 기능 (Feature)
- [ ] **인증**: 회원가입/로그인, JWT 액세스·리프레시 발급
- [ ] **관심종목 관리**: 등록/삭제, 내 관심종목 목록 조회
- [ ] **뉴스 수집**: AAPL부터 시작 → 관심종목으로 확장, 중복 제거 후 저장
- [ ] **뉴스 피드 API**: 내 관심종목 뉴스 리스트, 최신순, 페이지네이션
- [ ] **간단 요약 생성**: 규칙 기반(첫 N문장/요약 규칙)으로 요약 필드 저장
- [ ] **스케줄러**: 매일 09:00 수집·요약 배치 실행(초기 푸시는 생략)
- [ ] **헬스체크**: `/api/health/` 공개

### 운영/품질 (MVP 범위)
- [ ] **전역 보안 기본값**: JWT 인증 + `IsAuthenticated` / 공개 엔드포인트만 `AllowAny`
- [ ] **레이트 리밋**: anon 30/min, user 120/min
- [ ] **관리자(Admin)**: 뉴스/종목/유저 확인 및 삭제 가능
- [ ] **CORS**: 개발 허용(전체), 운영은 도메인 제한
- [ ] **DB**: 개발 SQLite, 배포 전 Postgres로 전환 준비(settings 분리)

---

## 🚀 Pro Requirements (v1.0 후보)

### 0) 품질 목표 (객관 지표)
- [ ] p95 응답시간 ≤ 200ms (뉴스 피드, 즐겨찾기)
- [ ] 에러율 < 0.5% / 가용성 ≥ 99.9%
- [ ] 부하: 동시 300 RPS, 1시간 지속 시 안정
- [ ] 테스트 커버리지 ≥ 80% (unit+integration)
- [ ] 보안 점검 통과(OWASP Top 10 기준 자가 체크)

### 1) 아키텍처/데이터
- [ ] DB: PostgreSQL 전환 + 마이그레이션 스크립트(데이터 유지)
- [ ] 인덱싱 전략: (ticker, published_at DESC), (user_id, created_at)
- [ ] 캐싱: Redis (뉴스 목록 캐시, 5~15분, 키 설계 포함)
- [ ] 비동기 파이프라인: Celery+Redis (수집/요약/중복제거/랭킹)
- [ ] 중복제거: 해시/URL 정상화(NFKC, UTM 제거) + 유사도 임계값

### 2) 기능 심화
- [ ] 관심종목 다중 소스 뉴스 집계(Finnhub + 보조 소스)
- [ ] 요약 고도화: 규칙→모델 사용 옵션(A/B), 길이·톤 컨트롤
- [ ] 랭킹: 최신성 + 출처 신뢰도 + 소셜 시그널(가중합) 스코어링
- [ ] 검색/필터: 키워드, 기간, 종목 필터 + 페이지네이션
- [ ] 사용자 설정: 요약 길이, 알림 시간대, 관심 키워드

### 3) 신뢰성/운영
- [ ] 재시도/백오프/데드레터 큐(Celery retry, max_retries, SQS 대체 가능)
- [ ] 헬스체크 확장: app/db/redis/finnhub 상태 세부 반환
- [ ] 로깅/모니터링: 구조화 로그(JSON), 요청 추적 ID, 메트릭스(p95, 큐 길이)
- [ ] 알람: 에러율/지연/큐 적체/외부 API 실패율 임계치 알림

### 4) 보안
- [ ] JWT: 짧은 Access(15–30m), Refresh(7–30d), 토큰 블랙리스트(로그아웃)
- [ ] 레이트리밋: anon 30/min, user 120/min + 로그인/회원가입 강화
- [ ] 웹훅/관리 엔드포인트: 서명검증(HMAC), IP 허용목록
- [ ] 비밀관리: .env 분리, 운영은 환경변수/Secret Manager
- [ ] 입력 검증/출처 화이트리스트/URL fetch SSRF 차단

### 5) CI/CD & 배포
- [ ] GitHub Actions: 테스트·린트·커버리지·도커 이미지 빌드
- [ ] 컨테이너: Dockerfile 멀티스테이지, docker-compose(로컬), prod 매니페스트
- [ ] 마이그레이션 자동 실행, 헬스체크 기반 롤링 배포
- [ ] 스테이징/프로덕션 분리, 설정 분리(settings module)

### 6) 테스트/문서/데모
- [ ] 테스트: 모델/서비스/뷰/비동기 태스크 통합 테스트
- [ ] 부하테스트: k6/Locust 스크립트와 리포트 첨부
- [ ] 보안 체크리스트 & 위험 시나리오 대응 문서
- [ ] 아키텍처 다이어그램/ERD/API 스펙(Swagger) 정리
- [ ] 데모 스크립트(시연 시나리오) + 스크린샷/짧은 영상

---

## 📎 Evidence
- [ ] 배포 URL + `/api/health/` 캡처
- [ ] README(실행법/ENV/ERD/API 예시)
- [ ] 테스트 15–20개, 커버리지 ≥60% 스샷
- [ ] `manage.py ingest_news` + cron 로그 캡처
- [ ] 중복제거 규칙 문서(URL 정규화/해시)
- [ ] 인덱스 근거 + 핵심 쿼리 예시(EXPLAIN 1–2개)
- [ ] Swagger/Redoc `/api/schema`
- [ ] Dockerfile + docker-compose(배포는 Postgres)

---

## 🔒 Non-Goals (이번 버전에서 제외)
- 실시간 푸시(FCM/APNS) • 자동 매매 • 차트/호가창 • 다국어/다국가 확장

## 🔧 ENV 목록(예정)
- DJANGO_SECRET_KEY, DEBUG, ALLOWED_HOSTS, DATABASE_URL(배포), FINNHUB_API_KEY

## ✅ MVP Done 기준
- 배포 URL 공개 + /api/health 200
- 관심종목 CRUD, 뉴스 수집/중복제거/요약, 피드 API(페이지네이션) 동작
- 테스트 ≥ 60%, Swagger/Redoc 제공
