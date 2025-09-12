## StockQ 백엔드 진행 현황 및 API 명세

이 문서는 완료된 기능, 제공 API, 출시 전 남은 작업을 정리합니다.

### 개요
- Django + DRF + SimpleJWT
- Celery + Beat (Redis 필요)
- Django Channels (channels-redis, Redis 필요)
- Finnhub 연동 (환경변수 `FINNHUB_API_KEY`)

### 인증/유저
- POST `/api/users/` — 회원가입
- POST `/api/users/token/` — JWT 발급
- POST `/api/users/token/refresh/` — 액세스 토큰 재발급
- GET `/api/users/me/` — 내 정보 조회

### 종목(Stocks)
- GET `/api/stocks/stocks/?q=` — 검색/목록(미지정 시 최대 100개)
- GET `/api/stocks/stocks/{id}/` — 단건 조회

### 즐겨찾기(Favorites)
- GET `/api/stocks/favorites/` — 목록
- POST `/api/stocks/favorites/` — 생성 `{ "stock_id": number }`
- DELETE `/api/stocks/favorites/{favoriteId}/` — 삭제

### 뉴스(News)
- GET `/api/stocks/news/?symbol=AAPL` — 심볼별 뉴스
- GET `/api/stocks/news/?favorites=true` — 내 즐겨찾기 종목의 뉴스 피드
- GET `/api/stocks/news/{id}/` — 단건 조회

### 알림(Notifications)
- GET `/api/stocks/notifications/` — 목록
- POST `/api/stocks/notifications/{id}/mark-read/` — 읽음 처리

### 요약(Summaries, 스캐폴드)
- POST `/api/stocks/summaries/generate/` — `{ "stock_id": number }` 요약 작업 큐잉(데모용)

### 백그라운드 작업
- 명령어: `manage.py fetch_us_stocks` — 미국 종목 메타 수집
- 명령어: `manage.py fetch_stock_data` — 즐겨찾기 종목 뉴스/시세 수집
- Celery Beat: 10분마다 `stocks.tasks.fetch_latest_news` 실행
- Channels WS: `ws://<host>:8000/ws/notifications/`

### 데이터 모델(요약)
- `users.User(email)`
- `stocks.Stock`, `FavoriteStock`, `News`, `Price`, `Summary`, `Notification`

### 완료됨
- JWT 인증 및 회원가입
- 종목 검색/상세(`is_favorite` 포함)
- 즐겨찾기 CRUD(RESTful 삭제)
- 뉴스 목록/상세(심볼/즐겨찾기 필터)
- Finnhub 연동 커맨드(심볼/뉴스/시세)
- Celery + Beat 주기 수집(10분 간격)
- 시그널 기반 알림 생성 + Channels 실시간 푸시
- Admin에 주요 모델 등록

### 출시 전 남은 작업
- 페이지네이션/정렬 기본값 확정 및 전역 설정
- 에러 응답 포맷 통일, OpenAPI(Swagger) 문서화
- WebSocket JWT 인증 미들웨어 정식화
- 실제 요약(OpenAI 등) 연동 및 캐싱/비용 제어
- 보안 하드닝(CORS allowlist, DEBUG=False, HTTPS 등)
- 테스트(단위/통합), 로깅/모니터링 도입
- 프로덕션 Docker/Compose(Nginx 리버스 프록시, 정적 파일)
- 운영 DB를 PostgreSQL로 전환
- 마이그레이션/시드 데이터/README 및 `.env.example` 정리


