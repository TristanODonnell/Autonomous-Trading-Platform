#!/usr/bin/env bash
# Install the host timers on Box A (run once, with sudo, from the repo checkout):
#
#   sudo infra/ops/install_timers.sh
#
# Units are generated for this checkout's path and its owner, so the repo can live anywhere.
# Safe to re-run after pulling changes.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_AS="$(stat -c %U "$REPO_DIR")"

cat > /etc/systemd/system/ratp-backup.service <<EOF
[Unit]
Description=RATP nightly Postgres backup to S3
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
User=$RUN_AS
# BACKUP_S3_BUCKET lives in infra/.env (see infra/.env.example)
EnvironmentFile=$REPO_DIR/infra/.env
ExecStart=$REPO_DIR/infra/ops/backup_postgres.sh
EOF

cat > /etc/systemd/system/ratp-backup.timer <<EOF
[Unit]
Description=RATP nightly Postgres backup (after the end-of-day chain)

[Timer]
OnCalendar=*-*-* 23:30:00 America/New_York
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now ratp-backup.timer
systemctl list-timers ratp-backup.timer --no-pager
