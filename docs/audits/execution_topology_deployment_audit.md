# Execution Topology Deployment Audit

**Purpose:** Determine where each piece of the platform should run — always-on service,
Airflow DAG, or on-demand worker — against the current single-EC2-instance Docker Compose
deployment, and flag what's missing before any infra file is edited.

**Status:** Audit/plan only. No `docker-compose.yml`, DAG, or deploy-pipeline files were
changed to produce this document.

**Scope:** The six components in the target split — FastAPI API, live/paper trading loop,
corporate actions, feature pipeline, intraday job, and the on-demand "worker box." A handful
of adjacent findings (stale docs, unrelated scheduler jobs) turned up during the inventory
and are flagged briefly in [Section 5](#5-out-of-scope-findings-worth-knowing-about) — they
are not deep-dived, since they weren't part of the six-item request.

---

## 1. Executive Summary

- **Three of the six components are already wired into the deployed Airflow instance.**
  `market_trading_dag`, `market_ingestion_dag`, and `corporate_action_ingestion_dag` already
  call the right functions every 5 minutes / daily. This is mostly a tuning job, not new
  build work.
- **The feature pipeline has no scheduled entry point anywhere.** `run_feature_pipeline_cycle`
  exists as a function and a CLI command, but no DAG calls it. This is the one clean gap that
  needs new Airflow wiring, not just a cron tweak.
- **Resolved: the split is on-demand vs. fully-scheduled, and it maps cleanly onto what's
  already built.** Present-day paper/live trading runs entirely on Airflow schedules — every
  piece (ingestion, corporate actions, features, trading cycle) is a DAG, with no standalone
  always-on daemon process. Backtests are on-demand — a single invocation that, for its own
  duration, runs its own internal simulated cadence (see `two_year_full.yaml`'s
  `scheduled_jobs:` block) with no dependency on Airflow at all. This also settles what was
  previously an open fork: the codebase's always-on daemon candidate (`runtime soak-loop
  paper`) is confirmed as a manual validation tool, not a production path. See
  [Section 3.0](#30-the-core-split--on-demand-backtests-vs-fully-scheduled-livepaper).
- **Corporate action timing has three different answers on record**, none of which is
  6:00am: the deployed DAG defaults to midnight UTC, an internal scheduler registry declares
  22:00 UTC (~6pm ET) as the intended cadence, and your checklist says ~6:00am. See
  [Section 3.3](#33-corporate-actions--decision-required).
- **The "worker box" is the `research` + `backtesting` CLI domains**, run against the same
  app image via `docker compose run`, not a separate service or Dockerfile. This part is
  low-risk and mostly just needs an invocation convention documented.
- **Three concrete deploy-blocking gaps**, independent of any of the above decisions:
  a required `JWT_SECRET` env var that isn't in `.env.example` and will crash the API on
  boot; no `alembic upgrade head` step anywhere in the build/deploy pipeline; and
  `.env.example`'s `DATABASE_URL` pointing at `localhost`, which is wrong for a containerized
  service on the Compose network. See [Section 4](#4-gaps--risks).

---

## 2. Component Inventory

| # | Component (your term) | Actual entrypoint | Currently wired to |
|---|---|---|---|
| 1 | FastAPI REST API | `autonomous_trading_platform.interfaces.rest.app:create_app` (uvicorn ASGI factory) | `Dockerfile` CMD + `docker-compose.dev.yml` only. **No service in production `docker-compose.yml`.** |
| 2 | Live/paper trading loop | `scheduler/cycles/run_trading_cycle.py:run_trading_cycle()` **or** `scheduler/orchestration/paper_trading_golden_path_orchestrator.py:PaperTradingGoldenPathOrchestrator` (via `runtime soak-loop paper`) | The first is wired to Airflow (`market_trading_dag`, every 5 min, weekdays). The second is wired to nothing but tests and the CLI. **These are two different things — see 3.2.** |
| 3 | Corporate actions | `scheduler/cycles/run_corporate_action_ingestion_cycle.py:run_corporate_action_ingestion_cycle()` | Airflow (`corporate_action_ingestion_dag`, `@daily`) |
| 4 | Feature pipeline | `scheduler/cycles/run_feature_pipeline_cycle.py:run_feature_pipeline_cycle()` | **Nothing.** No DAG exists. Only reachable via `atp features run-pipeline` or as a step inside the (unwired) golden-path orchestrator. |
| 5 | Intraday job | `scheduler/cycles/run_market_ingestion_cycle.py:run_market_ingestion_cycle()` | Airflow (`market_ingestion_dag`, every 5 min, every day) |
| 6 | Worker box | `research` + `backtesting` CLI domains; concretely `atp platform backtest run`, `atp runtime soak-loop backtest`, `atp research run-experiment` / `run-simulation` | Manual CLI invocation only (by design — this is correct for on-demand) |

All six entrypoints are real and implemented. One documentation note worth flagging up front:
`docs/backend/simulation/backtesting.md` currently says backtesting "is not yet implemented" —
this is stale. The `BacktestTradingCycleOrchestrator` and `PlatformBacktestRunner` are fully
built (confirmed by reading the code, not just the CLI docs). Worth a docs fix independent of
this infra work.

---

## 3. Per-Component Recommendation

### 3.0 The core split — on-demand backtests vs. fully-scheduled live/paper

This is the organizing principle for everything below, confirmed against
`fixtures/platform/replays/long/two_year_full.yaml` as the concrete reference case.

**Backtests are on-demand, and they bring their own internal schedule with them.**
`two_year_full.yaml` (invoked via `atp platform backtest run --fixture
fixtures/platform/replays/long/two_year_full.yaml`) is a two-year, 504-trading-day replay of
the full platform. It is a single on-demand invocation — but inside it, it declares its own
simulated cadence for every moving part, independent of Airflow entirely:

```yaml
scheduled_jobs:
  ingestion:          { cadence: daily,           enabled: true }
  corporate_actions:  { cadence: daily,           enabled: true }
  features:           { cadence: after_ingestion, enabled: true }
  trading_cycle:      { cadence: daily,           enabled: true }
  risk:               { cadence: daily,           enabled: true }
  governance:         { cadence: daily,           enabled: true }
  portfolio_snapshot: { cadence: daily,           enabled: true }
  operations_health:  { cadence: daily,           enabled: true }
  universe:           { cadence: monthly,         enabled: true }  # ~24 rotations
  research:           { cadence: monthly,         enabled: true }  # ~24 pipeline runs
```

This is exactly the "boots up with certain scheduled pieces when it runs" model: one
`docker compose run` invocation of the worker box owns its own miniature scheduler for the
life of that run (`PlatformBacktestRunner`'s day-by-day loop), then exits and releases
everything. It needs nothing from Airflow, and Airflow needs nothing from it. Worth one
precision note so the reference is exact rather than implying a literal 1:1 cadence with
production: `trading_cycle` is still replayed at 5-minute bar resolution inside each
simulated day (the fixture's header comment: "78 ticks/day × ~504 trading days ≈ 39,312
trading cycle calls"), matching production's intraday granularity. The other steps —
ingestion, corporate actions, features, risk, governance, portfolio snapshot, operations
health — run once per simulated day as a batch, which is the correct simplification for
replaying history in one pass rather than a difference to reconcile with production's
streaming 5-minute ingestion.

`two_year_full.yaml` isn't a one-off — it's one member of a fixture family under
`fixtures/platform/replays/` (`short/`, `medium/`, `long/`, plus `failure_injection/`,
`interactions/`, and `timeline_events/` for targeted scenario testing), and every fixture in
that family follows the same `platform_replay:` / `scheduled_jobs:` schema. Whatever
deployment convention gets documented for the worker box should be written generically
against that schema, not hardcoded to this one file.

**Present-day paper/live trading is the mirror image: everything scheduled, nothing
standalone.** Every piece that touches the live/paper environment — ingestion, corporate
actions, features, trading cycle — is an Airflow DAG, full stop. This directly settles what
was previously flagged as a decision in §3.2: the codebase's other candidate for "the trading
loop," the always-on `runtime soak-loop paper` daemon, is not part of the production
topology. It stays what it already functionally is — a manually-invoked validation harness
for paper-trading soak tests — never a `restart: unless-stopped` service, and never pointed
at the same database while the Airflow DAGs are active (§4, gap #4 explains why: the overlap
lock protecting against double-execution is process-local and can't see a second daemon
process).

Net effect on the rest of this document: the worker box (§3.6) gets simpler to reason about —
it's genuinely stateless between runs, so `docker compose run --rm app atp platform backtest
run --fixture <path>` is the whole story, no lingering process to manage. And the live/paper
side (§3.1–3.5) simplifies to "make sure every one of these is a DAG, tune the ones that
already are, build the one that isn't (feature pipeline)."

### 3.1 FastAPI REST API — Always-on service

**Recommendation:** New `app` service in `docker-compose.yml`, `restart: unless-stopped`,
built from the existing `Dockerfile` (no changes needed to the Dockerfile itself — its CMD
already runs `uvicorn autonomous_trading_platform.interfaces.rest.app:create_app --factory`).

This is the straightforward one. The image already builds correctly (proven by
`docker-compose.dev.yml` using it daily). The only work is adding the service block, and
closing the three gaps below before it will actually boot:

- `JWT_SECRET` must be set (`api/auth_middleware.py` does `os.environ["JWT_SECRET"]` — no
  default, hard crash on import if missing). Not present in `.env.example`.
- `DATABASE_URL` must resolve to the `postgres` service on the Compose network
  (`postgres:5432`), not `localhost:5433`. `.env.example` currently has the host-side value.
- Migrations must be applied before first request. Nothing currently runs
  `alembic -c infra/db/alembic.ini upgrade head` automatically anywhere — today this is a
  manual step per the README/CLAUDE.md commands.

One more thing worth a look, not a blocker: `app.py` hardcodes
`allow_origins=["http://localhost:5173"]` in the CORS middleware. That's fine as long as
nothing outside your dev machine needs to call the API through a browser. If you're planning
to serve the frontend from the EC2 box or a real domain later, this will need to change —
flagging it now since it's a one-line read, not because it's in scope for this pass.

### 3.2 Live/paper trading loop — Resolved: Airflow-only

Per §3.0: present-day paper/live is fully scheduled, no standalone daemon. This section keeps
the supporting evidence from the original audit, since it's the reasoning behind the
decision, not an open question anymore. There were two real, fully-built candidates in the
codebase:

**Option A — Airflow DAG (already deployed).** `market_trading_dag.py` calls
`run_trading_cycle()` every 5 minutes on weekdays (`*/5 * * * 1-5`), with retry count, retry
delay, and SLA all sourced from `Settings` (`trading_cycle_retry_attempts`,
`trading_cycle_retry_delay_seconds`, `trading_cycle_sla_seconds`). Inside
`run_trading_cycle.py`, the `TransientInfrastructureError` handler contains the comment
*"retry case → let Airflow retry"* — the function's own failure-handling design assumes
Airflow is the thing re-invoking it. This is strong internal evidence that Airflow is the
intended production executor for this path.

**Option B — Standalone daemon (`runtime soak-loop paper`).** `_PaperTradingSoakRunner` /
`PaperTradingGoldenPathOrchestrator` is a genuinely well-built always-on process: it
self-schedules using `RealMarketCalendar` / `RealTradingClock`, sleeps between ticks, handles
SIGTERM/SIGINT gracefully via `InterruptibleSleeper`, and in "realistic" mode runs an
intraday tick every 300 seconds during market hours, then an EOD maintenance pass once daily.
It would deploy cleanly as a `restart: unless-stopped` container. But:

- It's currently invoked **only** from tests and the CLI (`cli/commands/runtime_soak_loop.py`)
  — nothing wires it into any deployed entrypoint today.
- Its `run_intraday_tick()` chains `run_market_ingestion_cycle → run_feature_pipeline_cycle →
  run_trading_cycle` as one composite call. If this runs as a daemon *alongside* the existing
  Airflow DAGs, ingestion and trading would each fire twice per 5-minute window — once from
  Airflow, once from the daemon.
- The lock meant to prevent overlapping runs, `InMemoryNoOverlapLock`, is a plain in-process
  Python `set()` (confirmed by reading `scheduler/registry/no_overlap_lock.py`). It has no
  visibility into a second OS process. Running Option B next to Airflow's existing DAGs would
  not be caught or prevented by this lock at all.

**Confirmed: Option A.** Keep `run_trading_cycle` as the Airflow-scheduled path — it's already
deployed, already has matching retry/SLA config, and the code's own design assumes it. Don't
stand up the soak-loop daemon as a second production trading process. It's one more
persistent Python process on a 2 vCPU / 2 GiB box that's already running Postgres ×2, three
Airflow containers, and the Grafana LGTM bundle — and it buys you the same ~5-minute cadence
Airflow already gives you, not materially tighter latency.

`runtime soak-loop paper` stays what it already functions as: a manual validation harness for
paper-trading soak tests before promoting a strategy, run by a human, not as a standing
service. If it's ever run, don't point it at the same database while `market_trading_dag` /
`market_ingestion_dag` / `corporate_action_ingestion_dag` are active — either disable those
DAGs for the duration or point the soak run at a scratch database.

### 3.3 Corporate actions — Decision Required

**Recommendation once timing is confirmed:** keep as an Airflow DAG (already deployed) —
just retime it.

The gap here isn't wiring, it's that three different sources disagree on when this should
run, and none of them currently say 6:00am:

| Source | Schedule | In ET (approx, DST-dependent) |
|---|---|---|
| Deployed `corporate_action_ingestion_dag.py` | `schedule="@daily"` (Airflow default = midnight UTC) | ~7–8pm ET the *previous* evening |
| `scheduler/registry/scheduler_registry.py` (`SCHEDULER_REGISTRY`) | `cron="0 22 * * 1-5"` | ~5–6pm ET (shortly after market close) |
| Your checklist | ~6:00am | ~10–11am UTC |

The registry's 22:00 UTC reading lines up with a "process today's close, prep for tomorrow"
design — which is also what the golden-path orchestrator's `run_eod_maintenance()` does
(corporate actions → build `adjusted_bars` → compute adjusted-basis features), gated on an
18:00 ET "EOD window." None of the three currently implemented timings is a 6:00am pre-market
run.

This is a real semantic question, not just a cron string: is the intent "pull pending
corporate actions before the next session opens" (pre-market, your stated ~6am) or "finalize
today's session's adjustments after close" (post-market, what's actually built)? Both are
legitimate designs for different reasons. I'd default to keeping your stated pre-market intent
if there's no strong reason to prefer the built post-close model — but this is worth an
explicit decision rather than me silently picking one, since it changes what "the corporate
actions job" is actually for.

**Implementation note for whichever time you land on:** none of the DAGs currently set an
explicit `timezone=` on the `DAG(...)` call. A bare cron string is interpreted in UTC and
will *not* track ET's DST shifts — so "6am ET" as a fixed UTC cron will be off by an hour for
roughly half the year unless the DAG is given `timezone=pendulum.timezone("America/New_York")`
(Airflow 2.9 supports this) or you accept the seasonal drift.

### 3.4 Feature pipeline — Decision Required, needs new DAG

**Recommendation:** new Airflow DAG, not a standalone always-on service — but it needs to be
built with an explicit data dependency, not just a clock trigger.

This is the cleanest gap of the six: `run_feature_pipeline_cycle()` has no DAG at all today.
Two things worth knowing before building it:

**It isn't one job, it's two, with different cadences, and only one matches your checklist.**
Reading the code turned up:
- A **RAW-basis, returns-only, every-5-minute** flavor — this is what
  `run_intraday_tick()` computes inside the (unwired) golden-path orchestrator, and it matches
  `SCHEDULER_REGISTRY`'s declared `feature_pipeline_cycle` interval of 300 seconds.
- An **ADJUSTED-basis, full feature set, once-daily** flavor, computed after corporate
  actions — this is `run_eod_maintenance()`'s pattern, and it's the one that matches your
  "~6:30am, once a day" description.

**It isn't consumed by live trading, so it's lower risk than it looks.** I checked whether
`run_trading_cycle`'s evaluation step depends on these precomputed feature datasets — it
doesn't. `run_trading_evaluation_job.py` reads raw bars directly via `market_bar_reader` and
computes what it needs (e.g. recent closes for vol scaling) inline; it never touches
`ParquetFeatureRepository` or the "features" dataset. So the feature pipeline's consumers
appear to be research/ML tooling (dataset lineage, offline analysis), not the live trading
path. That means the missing DAG is a real gap for keeping research datasets current, but
it is **not** silently starving live trading of data — worth confirming that's your
understanding too, since it changes how urgently this needs to land.

**Dependency wiring:** `_validate_feature_pipeline_lineage()` enforces that an ADJUSTED-basis
feature run must source from an `adjusted_bars` dataset — which only exists after corporate
actions has run and produced one. This DAG can't just be "run at 6:30am" in isolation; it
needs the specific `dataset_version_id` corporate actions produced. Simplest approach given
this is a single-box, low-DAG-count deployment: fold this into the corporate-actions DAG as a
second sequential task (corp actions → feature pipeline), passing the `adjusted_bars`
dataset_version_id via XCom, rather than running two independently-scheduled DAGs and trying
to time them 30 minutes apart. Airflow 2.9's Datasets feature is the more decoupled
alternative if you'd rather keep them as separate DAGs.

**Open question:** do you also want the every-5-minute RAW flavor scheduled? It's currently
unscheduled in production too (nothing deployed calls it), and it's not part of your
six-item checklist, but it's the thing that would keep the RAW "features" dataset from going
stale intraday if anything downstream (research, dashboards) expects it fresh. Worth a
conscious yes/no rather than leaving it as a silent gap.

### 3.5 Intraday job — Scheduled (already wired, minor tuning)

**Recommendation:** keep as the existing Airflow DAG; consider tightening the cron.

This maps to `market_ingestion_dag.py` → `run_market_ingestion_cycle()`, already deployed,
`schedule="*/5 * * * *"`. This is the best fit for "intraday job, every 5 min, market hours"
in your checklist.

**Gap:** the cron has no restriction at all — it fires every 5 minutes, every day of the
year, including weekends. Compare to `market_trading_dag`, which at least restricts to
weekdays (`1-5`). I didn't find evidence either way on whether `run_market_ingestion_cycle`
internally no-ops gracefully outside market hours/weekdays — worth confirming before deciding
whether this is just "a bit wasteful" (it'll hit Alpaca and write empty/failed ingestion runs
on weekends) or worth tightening to `*/5 9-16 * * 1-5` (or similar) at the DAG level.

### 3.6 Worker box — On-demand, confirmed

**Recommendation:** no new service, no new Dockerfile. Invoke via
`docker compose run --rm app atp <command> ...` against the same image the API service uses.
Per §3.0, the flagship pattern is the `platform backtest` family driven by fixtures under
`fixtures/platform/replays/` — self-contained, brings its own internal cadence, needs nothing
running beyond Postgres.

Verified against `two_year_full.yaml`'s own header comment, the actual invocation is:

```bash
# Preview what the fixture would do without running it
docker compose run --rm app atp platform backtest plan \
  --fixture fixtures/platform/replays/long/two_year_full.yaml

# Run it, writing the artifact bundle to a host-mounted path
docker compose run --rm app atp platform backtest run \
  --fixture fixtures/platform/replays/long/two_year_full.yaml \
  --output artifacts/platform/backtests/two_year_full.json
```

No `--start`/`--end`/`--symbols` flags — the fixture is the complete spec (symbols, date
range, starting cash, seed strategies, governance settings, and its own `scheduled_jobs:`
cadence all live in the YAML). `artifacts/platform/backtests/` already has prior output from
this exact fixture on disk (`two_year_full.json`, `two_year_full.log`, plus an archived run
with a checkpoint file) — so this path is proven to work today, just not yet run
via Compose.

"The worker box" more broadly is the `research` and `backtesting` CLI domains:

| Use case | Command | What it does |
|---|---|---|
| Full-platform historical simulation (flagship) | `atp platform backtest run --fixture <path under fixtures/platform/replays/> --output <path>` | `PlatformBacktestRunner` — self-contained daily tick loop per §3.0. Produces a JSON artifact bundle. |
| Single-strategy backtest with DB persistence | `atp runtime soak-loop backtest --symbols ... --start ... --end ...` | `BacktestTradingCycleOrchestrator` — backfill → features → evaluation → simulated fills → snapshots, writes fills/positions/cash to DB. |
| Ad-hoc research simulation | `atp research run-simulation ...` / `atp research run-experiment ...` | `SimulationRunner` / `ExperimentOrchestrationService` — single-strategy or full experiment sweeps. |
| Extended research soak | `atp runtime soak-loop research --symbols ... --loop` | `HistoricalResearchGoldenPathOrchestrator` — long-running historical research pipeline. |

Because these all run inside the same app image and only need Postgres reachable, `docker
compose run --rm app atp ...` (one-shot container, no `restart:`, shares the network with
`postgres`) is the natural invocation — no standing resources reserved on the t4g.small
between runs. Worth noting for capacity planning: `two_year_full.yaml`'s own recommended-use
note calls it an "overnight run" (~39,000 simulated trading-cycle calls across 100 ingested
symbols) — on a 2 vCPU / 2 GiB box already carrying Postgres and Airflow, a long fixture like
this competing for CPU/memory with the live/paper DAGs at the same time is worth avoiding;
treat it as something you kick off when you're not also worried about tight trading-cycle
timing. If you'd rather have a documented shortcut than type the full `docker compose run`
invocation each time, a short wrapper script (not a new service) would do it — that's
implementation, not something to decide now.

One aside: the CLI docs (`docs/backend/cli/cli.md`) list 12 top-level domains and don't
include `platform` — but `atp platform backtest run/report/inspect` clearly exists and is
implemented (confirmed by reading `docs/audits/platform_backtest_output_audit.md`, and now by
the fixture and its artifacts on disk). Another small doc-drift data point, consistent with
the stale `backtesting.md` noted earlier.

---

## 4. Gaps & Risks

Ordered by how likely each is to actually break something.

| # | Gap | Severity | Detail |
|---|---|---|---|
| 1 | `JWT_SECRET` required, undocumented | **P0** | `api/auth_middleware.py` does `os.environ["JWT_SECRET"]` with no default. Not in `.env.example`. API will not boot without it. |
| 2 | No migration step in build/deploy | **P0** | No `alembic upgrade head` in `Dockerfile`, either `docker-compose*.yml`, or `.github/workflows/deploy.yml`. Today this is entirely manual (per the commands documented in `CLAUDE.md`). Adding an always-on `app` service doesn't fix this — it makes it more likely someone forgets, since there's no longer a human necessarily SSHing in before each deploy. |
| 3 | `.env.example` `DATABASE_URL` uses `localhost:5433` | **P0** | Wrong host for any service running *inside* the Compose network — needs `postgres:5432` (the service name + internal port). Confirm your real (non-committed) `.env` already accounts for this for any containerized use; the example file as documented doesn't. |
| 4 | Cross-process double-execution risk (design-time, avoided by the §3.0 split) | **P1** | `InMemoryNoOverlapLock` is process-local (a plain `set()`). Production never runs the daemon per §3.0/§3.2, so this doesn't come up there — but if `runtime soak-loop paper` is ever manually run against the same database while the Airflow DAGs are active, nothing prevents `market_ingestion_cycle`, `trading_cycle`, or `corporate_action_ingestion_cycle` from executing twice concurrently. See [3.2](#32-livepaper-trading-loop--resolved-airflow-only). |
| 5 | Feature pipeline has zero scheduled wiring | **P1** | See [3.4](#34-feature-pipeline--decision-required-needs-new-dag). Needs a new DAG plus an explicit dependency on corporate actions' `adjusted_bars` output — not just a time trigger. |
| 6 | Corporate-actions timing: 3-way mismatch + no DAG timezone | **P1** | See [3.3](#33-corporate-actions--decision-required). Also, no DAG sets `timezone=`, so any UTC cron will drift against ET wall-clock time across DST changes. |
| 7 | `market_ingestion_dag` has no weekday/market-hours cron restriction | **P2** | Runs `*/5 * * * *` every day including weekends. Confirm the job no-ops gracefully outside market hours before deciding whether this matters. |
| 8 | 8 more jobs declared in `SCHEDULER_REGISTRY` with no DAG or scheduler | **P2** | Not part of your six-item scope, flagging for awareness: `strategy_allocation_rebalance_cycle`, `strategy_auto_promotion_cycle`, `strategy_auto_demotion_cycle`, `factor_exposure_monitoring_cycle`, `factor_neutralization_verification_cycle`, `experiment_pipeline_cycle`, `correlation_monitoring_cycle`, `risk_budgeting_cycle`, `drawdown_governance_ladder_cycle`, `strategy_health_lifecycle_cycle` all have declared cron cadences in the registry but no Airflow DAG or other automatic trigger. Today they only run via manual `atp runtime trigger-job`. If any of these gate governance behavior you expect to be automatic (auto-promotion/demotion, drawdown ladder), it currently isn't. |
| 9 | CORS hardcoded to `localhost:5173` | **P2** | `interfaces/rest/app.py` — fine for now, will block a real frontend origin later. |
| 10 | Resource headroom on t4g.small (2 vCPU / 2 GiB) | **P3** | Already running `postgres`, `airflow_postgres`, `airflow-init/webserver/scheduler`, the Grafana LGTM bundle, and `otel-collector`. Adding the API service, plus periodic worker-box runs (§3.6) competing for the same CPU/memory, is worth watching via the already-deployed Grafana rather than assuming it'll fit. |
| 11 | No `entrypoint.sh` / command dispatch | **P3** | `Dockerfile` CMD is hardcoded to uvicorn. Every new service added to `docker-compose.yml` needs its own explicit `command:` override — same pattern `docker-compose.dev.yml` already uses, just flagging so it's not a surprise when there are more than one or two services on this image. |
| 12 | Doc drift | **P3 (hygiene)** | `docs/backend/simulation/backtesting.md` says backtesting "is not yet implemented" — it is. `docs/backend/cli/cli.md`'s domain list omits `platform`. That same file links to `docs/audits/agent-findings/cli_documentation_drift_audit.md`, which doesn't exist. Not infra-blocking, but worth a cleanup pass given `CLAUDE.md` already calls out that pages can drift from backend reality. |

---

## 5. Out-of-Scope Findings Worth Knowing About

Two things turned up that aren't part of the six-item request but are adjacent enough to
mention briefly rather than bury:

- **Gap #8 above** (8 unscheduled governance/risk cycles) — if any of these matter for how
  the platform is supposed to behave once it's running unattended, they'd need their own
  pass; not estimated or designed here.
- The `platform backtest` CLI domain (Section 3.6) is a more complete "run the whole platform
  historically" tool than `runtime soak-loop backtest`, and isn't mentioned in the CLI docs
  index at all. Worth knowing it exists if you're choosing what the worker box's primary tool
  should be.

---

## 6. Suggested Sequencing (once decisions above are made)

Not an implementation plan — just a rough order that respects the dependencies above:

1. Close the three P0 gaps (§4.1–4.3) — needed regardless of any other decision.
2. Decide corporate-actions timing (§3.3) and feature-pipeline scope (§3.4) — these determine
   what the new DAG work in step 4 actually looks like. (Trading-loop architecture is
   resolved per §3.0 — nothing to decide there.)
3. Add the `app` service to `docker-compose.yml` (§3.1).
4. Retime `corporate_action_ingestion_dag` and build the new feature-pipeline DAG as one
   dependent pair (§3.3, §3.4).
5. Tighten `market_ingestion_dag`'s cron if the market-hours question in §3.5/gap #7 comes
   back "yes, it matters."
6. Document the `docker compose run` invocation pattern for the worker box (§3.6), generalized
   across the `fixtures/platform/replays/` family rather than hardcoded to `two_year_full.yaml`
   — no code changes needed, just needs to be written down somewhere operators will find it.

---

## 7. Decisions Needing Your Input (consolidated)

**Resolved:** trading loop is Airflow-only, per §3.0 — present-day paper/live runs entirely
on Airflow schedules; `runtime soak-loop paper` is a manual validation tool, never a
standing service. No longer open.

Still open:

1. **Corporate actions timing:** pre-market ~6am (your stated intent) vs. post-close ~6pm
   (what the registry and golden-path orchestrator currently assume)? — §3.3
2. **Feature pipeline scope:** just the once-daily ADJUSTED flavor (matches your checklist),
   or also the every-5-minute RAW flavor (currently unscheduled anywhere, feeds research/ML
   dataset lineage)? Either way, per §3.0 it needs to land as an Airflow DAG, not a manual
   step — that part's no longer in question, only which flavor(s). — §3.4
3. **Intraday ingestion market-hours restriction:** worth tightening the cron, or does the
   job already no-op safely outside market hours? — §3.5, gap #7
