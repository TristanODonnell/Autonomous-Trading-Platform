# Postgres backup and restore (Box A)

## What runs

`infra/ops/backup_postgres.sh` runs nightly at 23:30 ET from a systemd timer on the host
(`ratp-backup.timer`). It dumps the platform database in `pg_dump` custom format, checks the
archive is readable, uploads it to `s3://$BACKUP_S3_BUCKET/postgres/YYYY/MM/ratp_<UTC stamp>.dump`
and deletes the local copy. If the upload fails the dump stays in `/var/tmp/ratp-backups`.

Parquet datasets under `data/` are not in this backup; they are published to S3 separately.

## One-time setup on the box

1. Install the AWS CLI and give the instance role `s3:PutObject` and `s3:GetObject` on the bucket.
2. Add `BACKUP_S3_BUCKET=<bucket>` to `infra/.env`.
3. Add a lifecycle rule on the bucket to expire old backups (the script never deletes from S3).
4. `sudo infra/ops/install_timers.sh`

## Checking it

```bash
systemctl list-timers ratp-backup.timer      # next and last run
journalctl -u ratp-backup.service -n 20      # last run's output
sudo systemctl start ratp-backup.service     # run one now
```

## Restore

Stop the app and scheduler first so nothing writes during the restore.

```bash
docker compose stop app scheduler
aws s3 cp s3://<bucket>/postgres/YYYY/MM/ratp_<stamp>.dump /var/tmp/restore.dump

# Restore into a fresh database, then swap it in
docker exec ratp_postgres psql -U ratp -d postgres -c "CREATE DATABASE ratp_restored"
docker exec -i ratp_postgres pg_restore -U ratp -d ratp_restored --no-owner --exit-on-error \
  < /var/tmp/restore.dump
docker exec ratp_postgres psql -U ratp -d postgres \
  -c "ALTER DATABASE ratp RENAME TO ratp_old" -c "ALTER DATABASE ratp_restored RENAME TO ratp"

docker compose up -d      # migrate brings an older backup up to the current schema
```

Drop `ratp_old` once the restored platform looks right.
