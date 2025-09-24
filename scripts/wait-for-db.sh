#!/bin/sh
set -e

# 환경변수에서 Postgres 접속 정보 가져오기 (없으면 기본값 사용)
: "${POSTGRES_USER:=stockq}"
: "${POSTGRES_DB:=stockq}"
: "${POSTGRES_HOST:=db}"
: "${POSTGRES_PORT:=5432}"

until pg_isready -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -q; do
  echo "⏳ Waiting for Postgres at $POSTGRES_HOST:$POSTGRES_PORT (user=$POSTGRES_USER, db=$POSTGRES_DB)..."
  sleep 2
done

echo "✅ Postgres is ready!"
exec "$@"
