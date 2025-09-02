# 📘 StockQ API 명세서 (MVP 기준)

---

## 📁 1. 인증 / Auth

| 기능 | 메서드 | URL | 요청 Body | 응답 | 비고 |
|------|--------|-----|------------|------|------|
| 회원가입 | `POST` | `/api/users/register/` | `{ "email", "password", "nickname" }` | `{ "id", "email", "nickname" }` | ✅ 완료 |
| 로그인 | `POST` | `/api/users/token/` | `{ "email", "password" }` | `{ "access", "refresh" }` | ✅ 완료 |
| 토큰 갱신 | `POST` | `/api/users/token/refresh/` | `{ "refresh" }` | `{ "access" }` | ✅ 완료 |
| 로그아웃 | `POST` | `/api/users/logout/` | `Authorization: Bearer` | `{ "success": true }` | 🔒 선택 구현 |
| 회원탈퇴 | `DELETE` | `/api/users/me/` | `Authorization: Bearer` | `{ "success": true }` | 🔒 soft delete |
| 내 정보 조회 | `GET` | `/api/users/me/` | `Authorization: Bearer` | `{ "id", "email", "nickname" }` | ✅ 완료 |

---

## 📁 2. 종목 / Stocks

| 기능 | 메서드 | URL | 요청/응답 | 비고 |
|------|--------|-----|------------|------|
| 종목 검색 | `GET` | `/api/stocks/search/?q=apple` | `[ { "symbol", "name" } ]` | 🔍 검색 기능 |
| 즐겨찾기 추가 | `POST` | `/api/stocks/favorites/` | `{ "symbol" }` | `{ "id", "symbol" }` | ✅ 완료 |
| 즐겨찾기 삭제 | `DELETE` | `/api/stocks/favorites/{favorite_id}/` | 없음 | `{ "success": true }` | ✅ 완료 |
| 즐겨찾기 목록 | `GET` | `/api/stocks/favorites/` | `[ { "id", "stock", "symbol" } ]` | ✅ 완료 |

---

## 📁 3. 뉴스 / News (요약)

| 기능 | 메서드 | URL | 요청 Body | 응답 | 비고 |
|------|--------|-----|------------|------|------|
| 뉴스 요약 (단일 심볼) | `GET` | `/api/news/summary/?symbol=AAPL&days=1` | 없음 | `[ { "title", "summary", "url", "published_at" } ]` | ✅ 완료 |
| 뉴스 요약 (전체 종목) | `GET` | `/api/news/summary/all/` | 없음 | `{ "AAPL": [...], "MSFT": [...] }` | 🧠 대시보드용 |
| GPT 요약 요청 | `POST` | `/api/news/summarize/` | `{ "article_text" }` | `{ "summary" }` | 🤖 GPT 연동 필요 |
| 뉴스 저장 요청 | `POST` | `/api/ingest/` | `{ "symbol" }` | `{ "success": true }` | 🧩 internal/celery task |

---

## 📁 4. 기타 유저 API

| 기능 | 메서드 | URL | 요청/응답 | 비고 |
|------|--------|-----|------------|------|
| 유저 충전 금액 조회 | `GET` | `/api/users/balance/` | `{ "balance": 10000 }` | 💰 결제 시 사용 예정 |
| 로그인 로그 기록 | `POST` | internal logging | `{ "user_id", "ip", "user_agent" }` | ⏱️ 선택 구현 |
| 튜터 찜 기능 | `POST/DELETE` | `/api/users/favorites/` | `{ "tutor_id" }` | ⭐ MiniClass용 옵션 |

---

## ✅ 현재 완료된 기능

- 회원가입, 로그인, 토큰 갱신
- 내 정보 조회
- 종목 즐겨찾기 CRUD
- 단일 종목 뉴스 요약
- curl 테스트 완료

---

## ⏳ 우선 구현 예정 기능

1. 로그아웃
2. 회원탈퇴 (`is_active=False`)
3. GPT 뉴스 요약 연동
4. 로그인 로그
5. 전체 종목 요약 API
6. 결제 시스템 (Stripe)
7. 유저 잔액 관련 API

---

## 📌 URL 네이밍 규칙 정리

| 목적 | Prefix 예시 |
|------|-------------|
| 인증 | `/api/users/token/` |
| 유저 정보 | `/api/users/me/` |
| 종목 | `/api/stocks/` |
| 즐겨찾기 | `/api/stocks/favorites/` |
| 뉴스 요약 | `/api/news/summary/` |

