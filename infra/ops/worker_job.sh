#!/usr/bin/env bash
# Run one `atp` job on the worker box, one at a time per DB.
#
#   infra/ops/worker_job.sh datasets pull --as-of 2026-10-12
#   infra/ops/worker_job.sh platform backtest run --fixture fixtures/... --output artifacts/...
#
# Takes the box-wide job lock (also held by the deploy while it migrates), then runs the
# command in a throw-away container from the current image. Exit code is the job's.
#
#   RATP_JOB_LOCK       lock file                       (default: /var/lock/ratp-worker-job.lock)
#   RATP_JOB_LOCK_WAIT  seconds to wait for the lock    (default: 3600; 0 = fail if busy)
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOCK="${RATP_JOB_LOCK:-/var/lock/ratp-worker-job.lock}"
WAIT="${RATP_JOB_LOCK_WAIT:-3600}"

if [[ $# -eq 0 ]]; then
  echo "usage: $0 <atp args...>" >&2
  exit 2
fi

log() { echo "[worker-job] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

exec 9>"$LOCK"
if ! flock -w "$WAIT" 9; then
  log "another job holds $LOCK after ${WAIT}s, giving up: atp $*" >&2
  exit 75  # EX_TEMPFAIL
fi

cd "$REPO_DIR"
log "start: atp $*"
set +e
docker compose -f docker-compose.worker.yml run --rm --no-deps worker atp "$@"
rc=$?
set -e
log "done (exit $rc): atp $*"
exit "$rc"
