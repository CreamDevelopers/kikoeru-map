#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
DB_DUMP="${1:?DB ダンプファイルを指定してください}"
MEDIA_TAR="${2:-}"

[ -f "$DB_DUMP" ] || { echo "not found: $DB_DUMP" >&2; exit 1; }
[ -z "$MEDIA_TAR" ] || [ -f "$MEDIA_TAR" ] || { echo "not found: $MEDIA_TAR" >&2; exit 1; }

set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a
DB="${POSTGRES_DB:-kikoeru}"
USER="${POSTGRES_USER:-kikoeru}"

if [ "${FORCE:-}" != "1" ]; then
  read -r -p "データベース $DB を $DB_DUMP の内容で置き換えます。よろしいですか？ [y/N] " ans
  [ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "中止しました"; exit 1; }
fi

echo "stopping app / worker / cloudflared ..."
docker compose stop cloudflared worker app

docker compose up -d db redis
until docker compose exec -T db pg_isready -U "$USER" -d "$DB" >/dev/null 2>&1; do sleep 1; done

echo "restoring database ..."
docker compose exec -T db psql -U "$USER" -d postgres -v ON_ERROR_STOP=1 \
  -c "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$DB' AND pid <> pg_backend_pid();" \
  -c "DROP DATABASE IF EXISTS \"$DB\";" \
  -c "CREATE DATABASE \"$DB\" OWNER \"$USER\";"
docker compose exec -T db pg_restore -U "$USER" -d "$DB" --no-owner --no-privileges --exit-on-error < "$DB_DUMP"

if [ -n "$MEDIA_TAR" ]; then
  echo "restoring media files ..."
  docker compose run --rm --no-deps -T --entrypoint sh app -c \
    'rm -rf /data/media && tar -C /data -xzf -' < "$MEDIA_TAR"
fi

docker compose exec -T redis redis-cli --scan --pattern 'pins*' | xargs -r docker compose exec -T redis redis-cli del >/dev/null
docker compose exec -T redis redis-cli del settings:v1 ngwords:v1 prefcounts >/dev/null

echo "starting services ..."
docker compose up -d
echo "restore done"
