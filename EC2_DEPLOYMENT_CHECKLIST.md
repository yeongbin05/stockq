# EC2 배포 체크리스트

## 🔴 백엔드 (EC2 서버) - 수정 필요

### 1. EC2 서버의 `.env` 파일 수정

MobaXterm으로 EC2 접속 후:
```bash
cd /path/to/stockq  # 프로젝트 폴더로 이동
nano .env  # 또는 vi .env
```

**추가/수정할 내용:**
```env
# EC2의 공개 IP 또는 도메인 (예: 3.34.123.45)
ALLOWED_HOSTS=your-ec2-public-ip

# 기존 환경 변수들도 확인
SECRET_KEY=your-secret-key
FINNHUB_API_KEY=your-finnhub-key
POSTGRES_DB=stockq
POSTGRES_USER=stockq
POSTGRES_PASSWORD=your-password
POSTGRES_HOST=db
REDIS_URL=redis://redis:6379/0
```

**저장 후:**
```bash
docker-compose -f docker-compose.prod.yml restart web
```

### 2. `prod.py` 확인 (수정 불필요 ✅)

현재 `prod.py`는 이미 환경 변수를 읽도록 되어 있습니다:
```python
ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").split(",") if os.getenv("ALLOWED_HOSTS") else ["*"]
```
→ `.env` 파일에 `ALLOWED_HOSTS`만 추가하면 자동으로 적용됩니다.

### 3. `base.py` - CORS 미들웨어 중복 제거 (선택사항)

현재 `base.py` 81번, 89번 줄에 `CorsMiddleware`가 중복으로 있습니다.
하지만 일단 작동은 하니 나중에 정리해도 됩니다.

---

## 🔴 프론트엔드 (로컬) - 수정 필요

### 1. 프론트엔드 `.env` 파일 생성/수정

프로젝트 루트 (`stockqrn-expo/`)에 `.env` 파일 생성:

```env
# EC2 서버 URL (예: http://3.34.123.45:8000 또는 https://api.yourdomain.com)
EXPO_PUBLIC_API_BASE_URL=http://your-ec2-ip:8000

# 카카오 JavaScript 키
EXPO_PUBLIC_KAKAO_JAVASCRIPT_KEY=your_kakao_javascript_key
```

### 2. 프론트엔드 빌드

```bash
cd stockqrn-expo
npm run build:web
```

빌드된 파일은 `web-build/` 폴더에 생성됩니다.

---

## ✅ 확인 사항

### EC2 보안 그룹
- 포트 8000이 열려있는지 확인
- 또는 80, 443 포트 (HTTPS 사용 시)

### 테스트
```bash
# EC2에서
curl http://localhost:8000/api/

# 로컬에서
curl http://your-ec2-ip:8000/api/
```

---

## 📝 요약

**EC2 서버에서:**
1. `.env` 파일에 `ALLOWED_HOSTS=your-ec2-ip` 추가
2. Docker 재시작

**로컬 프론트엔드에서:**
1. `.env` 파일에 `EXPO_PUBLIC_API_BASE_URL=http://your-ec2-ip:8000` 추가
2. 빌드 및 배포



