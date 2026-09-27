#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
DEST="${1:-./backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p "$DEST"

set -a
# shellcheck disable=SC1091
[ -f .env ] && . ./.env
set +a
DB="${POSTGRES_DB:-kikoeru}"
USER="${POSTGRES_USER:-kikoeru}"

echo "[$(date -Is)] backup start -> $DEST"

docker compose exec -T db pg_dump -U "$USER" -d "$DB" -Fc --no-owner --no-privileges \
  > "$DEST/db-$STAMP.dump.partial"
mv "$DEST/db-$STAMP.dump.partial" "$DEST/db-$STAMP.dump"

docker compose exec -T app tar -C /data -czf - media \
  > "$DEST/media-$STAMP.tar.gz.partial"
mv "$DEST/media-$STAMP.tar.gz.partial" "$DEST/media-$STAMP.tar.gz"

find "$DEST" -maxdepth 1 -type f \( -name 'db-*.dump' -o -name 'media-*.tar.gz' \) -mtime +"$KEEP_DAYS" -delete

echo "[$(date -Is)] backup done: db-$STAMP.dump media-$STAMP.tar.gz"
