# Operations CLI Domain Audit

Target CLI domain: `operations`
Target CLI file: `src/autonomous_trading_platform/cli/commands/operations.py`

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `operations verify-runtime-soak` | `--window-start` required, `--window-end` required, `--stale-after-minutes` default `15` | `handle_verify_runtime_soak` | yes | no | no | `LOCAL_DB_MUTATION` |

Notes:

- The command is operationally read-oriented, but `RuntimeSoakVerificationService` defaults `persist_report=True` and appends a row to `runtime_soak_reports`, so it is not read-only.
- The command reads runtime job, manifest, data freshness, reconciliation, control, risk, audit, and observability evidence from local DB and repo config files.
- It returns exit code `1` only for `FAILED`; `WARNING` returns `0`.

## 2. Domain Responsibility Check

| Command | Classification | Rationale |
|---|---|---|
| `operations verify-runtime-soak` | correctly placed | Operations is defined as operational verification, soak validation, runbooks, and health checks. This command verifies a runtime soak window after runtime/platform activity has already occurred. |

Related placement notes:

- The command should not move to `runtime`; runtime owns starting cycles/jobs/soak loops, while operations owns verifying their evidence after the fact.
- It could be wrapped by `platform` later as part of an end-to-end artifact bundle, but the standalone verifier belongs in `operations`.

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Classification | Implementation target service/function | Priority |
|---|---|---|---|---|---|
| `operations health` | Print lightweight system health equivalent to `/api/v1/system/health`. | Health checks are an operations responsibility. | read-only | `SystemHealthService.get_health` | P0 |
| `operations health-detailed` | Print detailed service health: OTEL, jobs, data pipeline, market session, broker, controls. | Detailed health is core operational verification. | read-only by default; broker-facing if broker client is enabled | `DetailedSystemHealthService.get_health` | P0 |
| `operations list-jobs` | List runtime job summaries: latest status, start/end, duration, error, run count. | Operators need job status without hitting REST. | read-only | `OperationsService.list_jobs` | P0 |
| `operations list-job-runs --job-name <name> --limit <n>` | Inspect recent runs for one runtime job with correlation links. | Operational debugging and monitoring. | read-only | `OperationsService.list_job_runs` | P0 |
| `operations runtime-state` | Show current runtime control state in operational view. | Operations routes already expose this for monitoring. | read-only, cross-domain state | `OperationsService.get_runtime_state` | P1 |
| `operations list-alerts` | List operational alerts with status/severity/category filters. | Alert triage belongs in operations. | read-only | `OperationalAlertService.list_alerts` | P0 |
| `operations acknowledge-alert --alert-id <id> --actor <user> [--note <text>]` | Acknowledge an operational alert. | Alert lifecycle action by operator. | local-mutating | `OperationalAlertService.acknowledge_alert` | P1 |
| `operations resolve-alert --alert-id <id> --actor <user> [--note <text>]` | Resolve an operational alert. | Alert lifecycle action by operator. | local-mutating | `OperationalAlertService.resolve_alert` | P1 |
| `operations snooze-alert --alert-id <id> --actor <user> --until <iso> [--note <text>]` | Temporarily suppress an alert. | Alert lifecycle action by operator. | local-mutating | `OperationalAlertService.snooze_alert` | P2 |
| `operations verify-runtime-soak --no-persist` | Run the soak verifier without writing `runtime_soak_reports`. | Enables safe local/read-only verification. | read-only | `RuntimeSoakVerificationService(..., persist_report=False)` | P0 |
| `operations verify-runtime-soak --output <path>` | Save the report to an artifact JSON file. | Verification output should be portable for runbooks/incidents. | local artifact output | existing report model dump | P1 |
| `operations latest-soak-report --environment <env>` | Show latest persisted soak report. | Completes the verify/readback loop. | read-only | `RuntimeSoakReportRepository.get_latest_for_environment` or equivalent | P1 |
| `operations runbook list` | List available operations runbooks. | Operations owns runbooks. | read-only | `docs/operations/runbooks/README.md` plus directory scan | P2 |
| `operations runbook show --name <name>` | Print a specific runbook path/summary. | Makes runbooks discoverable from the CLI. | read-only | docs lookup | P3 |

## 4. Testing Plan

Phase 0: `--help` commands

```powershell
atp operations --help
atp operations verify-runtime-soak --help
```

Phase 1: safe read-only commands

```powershell
# None currently registered.
# Proposed after extension:
atp operations health
atp operations list-jobs
atp operations list-job-runs --job-name trading_cycle --limit 20
atp operations list-alerts --status active --limit 50
```

Phase 2: local DB mutation commands

```powershell
$env:APP_ENV="test"
$env:DATABASE_URL="postgresql+psycopg://ratp:ratp@localhost:5432/ratp"
$env:TRADING_ENVIRONMENT="paper"
$env:NO_LIVE_TRADING="true"
$env:VALIDATE_BROKER_CONFIG="false"

atp operations verify-runtime-soak `
  --window-start 2026-05-08T12:00:00Z `
  --window-end 2026-05-08T12:30:00Z `
  --stale-after-minutes 15
```

Phase 3: cross-domain/runtime commands

```powershell
# Generate runtime evidence first, then verify it.
atp runtime soak-loop paper --mode single

atp operations verify-runtime-soak `
  --window-start 2026-05-08T12:00:00Z `
  --window-end 2026-05-08T13:00:00Z
```

Phase 4: broker/external commands if applicable

```powershell
# Current operations CLI has no direct broker/API command.
# Proposed detailed health may become broker-facing depending on broker client construction:
atp operations health-detailed --include-broker
```

## 5. Risks / Suspicious Wiring

- No handler signature mismatches found.
- No parser-required handler inputs are missing.
- No obvious placeholder commands in the file.
- `verify-runtime-soak` sounds read-only, but it persists a report by default; the name does not make the DB write obvious.
- The command lacks `--dry-run` or `--no-persist`, even though it is a verifier and would naturally be used in safe local/read-only checks.
- `--stale-after-minutes` has no lower/upper validation. Zero or negative values can create misleading stale-state results.
- `--window-start` and `--window-end` are parsed at handler time, not argparse type time; invalid values fail only after dependency construction.
- The command constructs `Settings()` even though it only needs environment. This can make verification fail on unrelated config validation.
- The verifier checks many cross-domain invariants: runtime jobs, trading manifests, data freshness, reconciliation, fills, cash/positions/equity, governance, replay, risk/control state, audit events, and local observability config. That is appropriate for operations, but the command should document this breadth.
- The verifier reads local collector config files for metrics/traces/Loki wiring; this is local file inspection, not a live collector check.
- The command emits JSON to stdout but cannot write an artifact file.
- Warning reports exit `0`; that is reasonable for non-fatal degradation, but CI/runbook usage may need `--fail-on-warning`.
- Mutating alert lifecycle functionality exists in REST/services, but there is no operations CLI wrapper for it.

## 6. Recommended Refactor / Extension

Keep the domain and keep `operations verify-runtime-soak`, but make its behavior explicit:

- Add `--no-persist` or `--dry-run` for read-only verification.
- Add `--output <path>` for artifact output.
- Add `--fail-on-warning` for CI/runbook use.
- Validate `--stale-after-minutes` with a positive bounded range.
- Prefer lightweight dependency construction so a local soak report does not depend on unrelated full settings validation.

Add the missing operational read commands backed by existing REST/application services:

- `operations health`
- `operations health-detailed`
- `operations list-jobs`
- `operations list-job-runs`
- `operations runtime-state`
- `operations list-alerts`

Add alert lifecycle commands with audit logging already supplied by `OperationalAlertService`:

- `operations acknowledge-alert`
- `operations resolve-alert`
- `operations snooze-alert`
- `operations unsnooze-alert`
- `operations add-alert-note`

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `operations verify-runtime-soak` | Functional soak verifier; persists report and returns nonzero only on failed status | yes | Medium: mutates DB without an explicit no-persist/dry-run mode; broad cross-domain checks | Keep; add `--no-persist`, artifact output, stronger argument validation, and optional `--fail-on-warning` |
