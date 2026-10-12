#!/usr/bin/env bash
# Install the worker box's host timers (run once, with sudo, from the repo checkout):
#
#   sudo infra/ops/install_worker_timers.sh
#
# Nightly dataset pull from S3 after Box A's end-of-day publish (A finishes by ~18:30 ET;
# 20:30 ET leaves margin for a slow chain). Weekly/monthly research jobs are added here in
# Phase 2.3 / Phase 4 as further timers calling infra/ops/worker_job.sh.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_AS="$(stat -c %U "$REPO_DIR")"

cat > /etc/systemd/system/ratp-worker-pull.service <<EOF
[Unit]
Description=RATP worker: pull published datasets from S3
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$RUN_AS
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/infra/ops/worker_job.sh datasets pull
EOF

cat > /etc/systemd/system/ratp-worker-pull.timer <<EOF
[Unit]
Description=RATP worker nightly dataset pull (after Box A's publish)

[Timer]
OnCalendar=*-*-* 20:30:00 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now ratp-worker-pull.timer
systemctl list-timers 'ratp-worker-*' --no-pager
