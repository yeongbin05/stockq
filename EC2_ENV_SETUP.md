# EC2 서버 .env 파일 설정 가이드

EC2 서버에 SSH 접속 후, 프로젝트 루트 디렉토리의 `.env` 파일을 수정하세요.

## 필수 수정 사항

### 1. ALLOWED_HOSTS
```env
ALLOWED_HOSTS=your-ec2-public-ip,ec2-xxx-xxx-xxx-xxx.compute-1.amazonaws.com
```
- EC2의 공개 IP 주소 또는 도메인을 입력
- 예: `ALLOWED_HOSTS=3.34.123.45` 또는 `ALLOWED_HOSTS=api.yourdomain.com`
- 여러 개는 쉼표로 구분: `ALLOWED_HOSTS=3.34.123.45,api.yourdomain.com`

### 2. 기존 환경 변수 확인
다음 변수들이 제대로 설정되어 있는지 확인:
- `SECRET_KEY` - Django 시크릿 키
- `FINNHUB_API_KEY` - Finnhub API 키
- `POSTGRES_DB` - 데이터베이스 이름
- `POSTGRES_USER` - 데이터베이스 사용자
- `POSTGRES_PASSWORD` - 데이터베이스 비밀번호
- `POSTGRES_HOST` - 데이터베이스 호스트 (보통 `db` 또는 `localhost`)
- `REDIS_URL` - Redis URL (보통 `redis://redis:6379/0`)

## 수정 후 재시작

```bash
# Docker Compose를 사용하는 경우
docker-compose -f docker-compose.prod.yml restart web

# 또는 전체 재시작
docker-compose -f docker-compose.prod.yml down
docker-compose -f docker-compose.prod.yml up -d
```

## 확인 방법

서버가 정상 작동하는지 확인:
```bash
curl http://localhost:8000/api/
```

또는 브라우저에서:
```
http://your-ec2-ip:8000/api/
```


