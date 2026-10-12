# Worker box (Box B)

The research engine: pulls the Parquet datasets Box A publishes to S3, runs backtests,
bench re-sims and strategy generation against its **own** Postgres, and (from Phase 3)
hands results to Box A through A's REST API. It never trades and never touches A's DB.

Plan and decisions: `docs/roadmaps/pre-launch-deployment-plan.md` (§1, §2, Phase 2).

## What runs

| Piece | Where | Notes |
|---|---|---|
| Postgres | `ratp_worker_postgres` (compose) | Own volume `ratp_worker_pgdata`; port 5433 bound to localhost only |
| Migrations | `ratp_worker_migrate` (compose, one-shot) | `alembic upgrade head` on every deploy |
| Jobs | `docker compose run --rm worker atp ...` | Always via `infra/ops/worker_job.sh`, which holds the one-job-at-a-time lock |
| Nightly dataset pull | `ratp-worker-pull.timer`, 20:30 ET | `atp datasets pull`; see below |
| OTel collector | `ratp_worker_otel_collector` | Forwards to Box A's collector over the VPC; host + container metrics tagged `deployment.environment=backtest` |
| Deploy | `.github/workflows/deploy-worker.yml` | CI green on main → ssh → `up -d --build`; no market-hours freeze; waits for a running job |

There is no API, scheduler, Grafana or backup on this box. Everything it holds can be
rebuilt from S3 (datasets) and a re-run (results), so its Postgres is not backed up.

## One-time AWS setup

1. **Instance.** Same region as Box A (us-east-2), Ubuntu arm64, start with a t4g.medium
   (research jobs are memory-hungry; see the dev-box DuckDB history). Same key pair is fine,
   or a new one: put its private key in the `WORKER_SSH_KEY` GitHub secret either way.
2. **IAM.** Policy `ratp-worker-s3`: `s3:ListBucket` on `arn:aws:s3:::<bucket>`,
   `s3:GetObject` + `s3:PutObject` on `arn:aws:s3:::<bucket>/*` (Put is for run artifacts in
   2.3). Role `ratp-worker` (EC2 trusted entity) with that policy, attached to the instance.
   **Metadata hop limit 2** on the instance so containers can use the role.
3. **Networking.** Box B's security group: inbound 22 from the deploy runners (same as A).
   **Box A's** security group: add inbound TCP 4317 with source = Box B's security group, so
   the worker's telemetry reaches A's collector. B reaches A on A's **private** IP.
4. **GitHub secrets.** `WORKER_HOST` (B's public IP or Elastic IP), `WORKER_SSH_KEY`.
   `EC2_USERNAME` is shared with A (`ubuntu`). Until `WORKER_HOST` exists the worker deploy
   workflow skips itself.

## One-time box setup

```bash
# Docker + compose plugin + AWS CLI, as on Box A
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 awscli
sudo usermod -aG docker ubuntu   # log out and in again

git clone https://github.com/TristanODonnell/Autonomous-Trading-Platform.git ~/Autonomous-Trading-Platform
cd ~/Autonomous-Trading-Platform
cp infra/.env.example infra/.env           # set a password of its own
cp .env.worker.example .env.worker         # set DATASET_S3_BUCKET, BOX_A_OTLP_ENDPOINT (A's private IP)

aws sts get-caller-identity                # must show the ratp-worker role
docker compose -f docker-compose.worker.yml up -d --build
docker compose -f docker-compose.worker.yml run --rm --no-deps worker \
  python -c "import boto3; print(boto3.client('sts').get_caller_identity()['Arn'])"

sudo infra/ops/install_worker_timers.sh
infra/ops/worker_job.sh datasets list-remote
infra/ops/worker_job.sh datasets pull      # first pull: everything A has published so far
```

## Datasets: how the pull works

Box A's end-of-day chain uploads each immutable `dataset_version=<id>` directory to
`s3://<bucket>/datasets/<same path>` and then a manifest `datasets/manifests/<id>.json`
(files, sizes, SHA-256s, and the `dataset_versions` registry row). Last it writes the day's
index `datasets/published/<YYYY-MM-DD>.json` listing everything it held after that session.

`atp datasets pull` reads the manifests, downloads any file missing locally (verified by
size and hash; partial downloads never replace a file), and inserts the registry row into
**this box's** `dataset_versions`, with `metadata_json.pulled_from` recording the manifest
and A's git SHA. It is idempotent and never deletes.

```bash
infra/ops/worker_job.sh datasets list-remote                    # every published version
infra/ops/worker_job.sh datasets list-remote --as-of 2026-10-12 # the set A held after that session
infra/ops/worker_job.sh datasets pull                           # everything (the nightly default)
infra/ops/worker_job.sh datasets pull --as-of 2026-10-12        # pin a run to one session's set
infra/ops/worker_job.sh datasets pull --version <id>            # one version
```

Pin a research campaign to `--as-of <date>` and record that date with the run so a re-sim
sees exactly the data A had then.

**Seeding before A's first publish:** from a dev machine that has Parquet under `data/` and
AWS credentials with write access to the bucket, `atp datasets publish --trading-date <date>`
uploads the same way the box does. Dev versions are then visible to the worker like any
other; A skips versions that already have a manifest.

## Running a fixture: `atp worker run`

The job runner does the whole sequence and leaves a summary whatever happens:

```
pull datasets (pinned with --as-of) → reset DB state → drop the stale checkpoint
→ atp platform backtest run → upload the bundle → write + upload summary.json
```

```bash
infra/ops/worker_job.sh worker run --fixture fixtures/platform/replays/short/smoke_minimal.yaml
infra/ops/worker_job.sh worker run --fixture fixtures/platform/replays/long/six_month_regression.yaml   --name six_month_2026w42 --as-of 2026-10-12
infra/ops/worker_job.sh worker run --fixture ... --name six_month_2026w42 --resume   # after a crash
infra/ops/worker_job.sh worker reset                                                 # state wipe only
```

- The run name (default: the fixture file's stem) names the bundle
  `artifacts/platform/backtests/<name>.json`, its checkpoint, and the S3 folder
  `s3://<bucket>/artifacts/worker/<name>/<UTC stamp>/` holding `<name>.json` and `summary.json`.
- `summary.json` (also at `artifacts/worker/runs/<name>/<stamp>/`) records each step's status
  and duration, the dataset pin, git SHA, host, exit code and the bundle's headline numbers
  (ticks, orders, error count). A run that fails at the pull step still produces one.
- Status is `ok`, `completed_with_errors` (bundle written, backtest exit 1) or `failed`.
- The reset keeps the `dataset_versions` rows the pull registered and never touches Parquet.
  Both `worker run` and `worker reset` refuse to run unless `APP_ENV` is backtest, dev or test.
- `--resume` keeps the checkpoint and skips the reset; `--no-pull` / `--no-upload` for offline work.

## Checking it

```bash
systemctl list-timers 'ratp-worker-*'                 # next / last pull
journalctl -u ratp-worker-pull.service -n 30          # last pull's output
docker compose -f docker-compose.worker.yml ps
docker compose -f docker-compose.worker.yml logs otel-collector --tail 20   # export errors to A
psql "$DATABASE_URL" -c "select dataset_version_id, dataset_name, validation_status from dataset_versions order by created_at desc limit 10"
```

Grafana on Box A: host and container panels filter by `host_name`; the worker's series carry
`deployment_environment="backtest"`, which the alert rules exclude.

## Known gaps (Phase 2.3+)

- **Fixture replays need a market-data key pair.** By design (Step 5c parity) the platform
  backtester runs the real ingestion and trading-cycle code, so a replay fetches its bars
  through the Alpaca data API exactly as Box A does; that is what makes a backtest result
  carry over to paper and live. Put a key pair from a separate paper account in
  `.env.worker` (`PAPER_BROKER_API_KEY/SECRET`, data access only; the box never submits
  orders). The pulled Parquet feeds the research jobs (bench re-sims, strategy generation,
  regime suites), which are the worker's main work. A recorded-dataset replay mode is
  planned before scheduled replays (deployment plan, 3.4).
- No weekly/monthly research timers; add them to `install_worker_timers.sh` via `worker_job.sh`
  calling `worker run`.
- Span/log attributes for runs (`run.mode`, `backtest.run_id`, `fixture`) and the Worker
  dashboard are not done.
- Backtest traces are sampled at 10% by the collector; revisit once the Worker dashboard exists.
