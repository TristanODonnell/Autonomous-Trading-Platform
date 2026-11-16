#!/usr/bin/env bash
# Run one `atp` job on the worker box, one at a time per DB.
#
#   infra/ops/worker_job.sh datasets pull --as-of 2026-10-12
#   infra/ops/worker_job.sh platform backtest run --fixture fixtures/... --output artifacts/...
#
# Takes the box-wide job lock (also held by the deploy while it migrates), then runs the
# command in a throw-away container from the current image. Exit code is the job's.
#
# The job runs under a memory ceiling: the box's memory minus a reserve for Postgres, the
# collector and the OS. A job that outgrows it is killed by itself (exit 137) and the rest
# of the box keeps running; without a ceiling the kernel picks what to kill. The default
# follows the box, so a larger instance raises it with no change here.
#
#   RATP_JOB_LOCK       lock file                       (default: /var/lock/ratp-worker-job.lock)
#   RATP_JOB_LOCK_WAIT  seconds to wait for the lock    (default: 3600; 0 = fail if busy)
#   RATP_JOB_MEMORY     memory ceiling, Docker units    (default: box memory minus the
#                       reserve, e.g. 3055m; 0 = no ceiling)
#   RATP_JOB_MEMORY_RESERVE_MB  kept back from the job  (default: 768)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK="${RATP_JOB_LOCK:-/var/lock/ratp-worker-job.lock}"
WAIT="${RATP_JOB_LOCK_WAIT:-3600}"

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <atp args...>" >&2
  exit 2
fi

log() { echo "[worker-job] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

if [[ -z "${RATP_JOB_MEMORY:-}" ]]; then
  total_mb="$(awk '/^MemTotal:/ {print int($2 / 1024)}' /proc/meminfo)"
  ceiling_mb=$(( total_mb - ${RATP_JOB_MEMORY_RESERVE_MB:-768} ))
  if (( ceiling_mb < 256 )); then
    log "box has ${total_mb} MB: nothing left for a job after the reserve" >&2
    exit 2
  fi
  RATP_JOB_MEMORY="${ceiling_mb}m"
fi
export RATP_JOB_MEMORY  # read by docker-compose.worker.yml (worker.mem_limit)

exec 9>"$LOCK"
if ! flock -w "$WAIT" 9; then
  log "another job holds $LOCK after ${WAIT}s, giving up: atp $*" >&2
  exit 75  # EX_TEMPFAIL
fi

cd "$REPO_DIR"
log "start (memory ceiling $RATP_JOB_MEMORY): atp $*"
set +e
docker compose -f docker-compose.worker.yml run --rm --no-deps worker atp "$@"
rc=$?
set -e
if [[ $rc -eq 137 ]]; then
  log "killed (exit 137): the usual cause is the memory ceiling ($RATP_JOB_MEMORY)," \
    "see the memory section of docs/operations/runbooks/worker-box.md" >&2
fi
log "done (exit $rc): atp $*"
exit "$rc"
