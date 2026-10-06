#!/usr/bin/env bash
# Weekly disk retention on Box A (host timer, see install_timers.sh).
#
#   infra/ops/retention.sh            # delete
#   infra/ops/retention.sh --dry-run  # only print what would go
#
# Removes: local backup dumps older than BACKUP_DUMP_KEEP_DAYS (14), files under artifacts/
# older than ARTIFACT_KEEP_DAYS (30), and Docker images/build cache unused for 14 days.
# Parquet datasets under data/ are never touched here (see pre-launch plan 1.3 §2).
# Container logs are capped by the logging options in docker-compose.yml, not by this script.
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/tmp/ratp-backups}"
BACKUP_DUMP_KEEP_DAYS="${BACKUP_DUMP_KEEP_DAYS:-14}"
ARTIFACT_KEEP_DAYS="${ARTIFACT_KEEP_DAYS:-30}"
DOCKER_PRUNE_HOURS="${DOCKER_PRUNE_HOURS:-336}"  # 14 days

log() { echo "[retention] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

prune_files() {
  local label="$1" dir="$2" days="$3"
  if [[ ! -d "$dir" ]]; then
    log "$label: $dir does not exist, skipping"
    return
  fi
  local count
  count="$(find "$dir" -type f -mtime "+$days" | wc -l | tr -d ' ')"
  if [[ "$DRY_RUN" == 1 ]]; then
    log "$label: would delete $count file(s) older than ${days}d under $dir"
    find "$dir" -type f -mtime "+$days" -print | sed 's/^/  /'
  else
    find "$dir" -type f -mtime "+$days" -print -delete | sed 's/^/  deleted /'
    find "$dir" -mindepth 1 -type d -empty -delete
    log "$label: deleted $count file(s) older than ${days}d under $dir"
  fi
}

prune_files "backup dumps" "$BACKUP_DIR" "$BACKUP_DUMP_KEEP_DAYS"
prune_files "artifacts" "$REPO_DIR/artifacts" "$ARTIFACT_KEEP_DAYS"

if [[ "$DRY_RUN" == 1 ]]; then
  log "docker: would prune dangling images and build cache unused for ${DOCKER_PRUNE_HOURS}h"
  docker image ls --filter dangling=true --format '  dangling image {{.ID}} {{.Size}} (created {{.CreatedSince}})'
else
  # Dangling images only: the previous app image stays available for a rollback.
  docker image prune -f --filter "until=${DOCKER_PRUNE_HOURS}h" | tail -1 | sed 's/^/  images: /'
  docker builder prune -f --filter "until=${DOCKER_PRUNE_HOURS}h" | tail -1 | sed 's/^/  build cache: /'
  log "docker: pruned"
fi

log "disk: $(df -h / | awk 'NR==2 {print $4 " free of " $2 " (" $5 " used)"}')"
