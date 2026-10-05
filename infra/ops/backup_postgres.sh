#!/usr/bin/env bash
# Nightly platform Postgres backup: pg_dump (custom format) -> verify -> S3.
#
# Runs on the host from a systemd timer (see install_timers.sh), not inside the app, so it
# keeps working when the app or scheduler is unhealthy.
#
#   BACKUP_S3_BUCKET   required unless --no-upload
#   BACKUP_S3_PREFIX   key prefix inside the bucket      (default: postgres)
#   BACKUP_DIR         where the dump is staged locally  (default: /var/tmp/ratp-backups)
#   POSTGRES_CONTAINER container to dump                 (default: ratp_postgres)
#
# The dump is removed after a successful upload. Old backups are expired by an S3 lifecycle
# rule on the bucket, not by this script. Restore: docs/operations/runbooks/postgres-backup-restore.md
set -euo pipefail

UPLOAD=1
if [[ "${1:-}" == "--no-upload" ]]; then
  UPLOAD=0
fi

CONTAINER="${POSTGRES_CONTAINER:-ratp_postgres}"
BACKUP_DIR="${BACKUP_DIR:-/var/tmp/ratp-backups}"
PREFIX="${BACKUP_S3_PREFIX:-postgres}"

if [[ "$UPLOAD" == 1 && -z "${BACKUP_S3_BUCKET:-}" ]]; then
  echo "[backup] BACKUP_S3_BUCKET is not set" >&2
  exit 2
fi

log() { echo "[backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$BACKUP_DIR"
dump="$BACKUP_DIR/ratp_${stamp}.dump"
trap 'rm -f "$dump.partial"' EXIT

# POSTGRES_USER / POSTGRES_DB are already set inside the container (infra/.env).
log "dumping from $CONTAINER"
docker exec "$CONTAINER" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' \
  > "$dump.partial"

# A truncated or corrupt archive fails here, before it can replace a good backup in S3.
entries="$(docker exec -i "$CONTAINER" pg_restore --list < "$dump.partial" | grep -c ' TABLE DATA ')"
if [[ "$entries" -eq 0 ]]; then
  log "dump has no table data, refusing to keep it" >&2
  exit 1
fi
mv "$dump.partial" "$dump"
log "dump ok: $(du -h "$dump" | cut -f1), $entries tables"

if [[ "$UPLOAD" == 0 ]]; then
  log "--no-upload: left at $dump"
  exit 0
fi

dest="s3://${BACKUP_S3_BUCKET}/${PREFIX}/$(date -u +%Y/%m)/$(basename "$dump")"
aws s3 cp --only-show-errors "$dump" "$dest"
rm -f "$dump"
log "uploaded to $dest"
