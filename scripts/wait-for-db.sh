#!/bin/sh
set -e

until pg_isready -h db -p 5432 -U stockq; do
  echo "⏳ Waiting for Postgres..."
  sleep 2
done

exec "$@"
