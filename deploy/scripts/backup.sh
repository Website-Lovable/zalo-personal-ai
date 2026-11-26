#!/usr/bin/env bash
# Compressed PostgreSQL dump into ./backups, keeping the last BACKUP_KEEP_DAYS days.
# Run daily from cron, e.g.:  15 2 * * *  cd /opt/zalo-ai && deploy/scripts/backup.sh
# Copy ./backups off the server too (object storage / another machine): a backup that lives
# only on the same disk does not survive losing that disk.
set -euo pipefail

cd "$(dirname "$0")/../.."
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
mkdir -p backups
chmod 700 backups
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FILE="backups/zalo_ai-$STAMP.sql.gz"

docker compose -f docker-compose.prod.yml --env-file .env.production exec -T db \
  sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --format=plain' \
  | gzip -9 > "$FILE.partial"
mv "$FILE.partial" "$FILE"
chmod 600 "$FILE"
echo "Backup written: $FILE ($(du -h "$FILE" | cut -f1))"

find backups -name 'zalo_ai-*.sql.gz' -mtime +"$KEEP_DAYS" -print -delete
