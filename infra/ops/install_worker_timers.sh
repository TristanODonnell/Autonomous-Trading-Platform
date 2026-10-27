#!/usr/bin/env bash
# Install the worker box's host timers (run once, with sudo, from the repo checkout):
#
#   sudo infra/ops/install_worker_timers.sh
#
# Nightly dataset pull from S3 after Box A's end-of-day publish (A finishes by ~18:30 ET;
# 20:30 ET leaves margin for a slow chain).
#
# Weekly bench re-sim (plan 3.3a): Saturday morning, after Friday's pull and well before
# Box A's weekly portfolio review on Monday's close. Asks A what to re-simulate, runs it,
# posts the result; A decides whether to accept it (worker_results_enabled, dataset age,
# git SHA). Monthly research jobs are added the same way in Phase 4.
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

cat > /etc/systemd/system/ratp-worker-bench-resim.service <<EOF
[Unit]
Description=RATP worker: weekly bench re-sim for Box A's portfolio review
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$RUN_AS
WorkingDirectory=$REPO_DIR
ExecStart=$REPO_DIR/infra/ops/worker_job.sh worker bench-resim
EOF

cat > /etc/systemd/system/ratp-worker-bench-resim.timer <<EOF
[Unit]
Description=RATP worker weekly bench re-sim (Saturday, after Friday's pull)

[Timer]
OnCalendar=Sat *-*-* 06:00:00 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now ratp-worker-pull.timer
systemctl enable --now ratp-worker-bench-resim.timer
systemctl list-timers 'ratp-worker-*' --no-pager
