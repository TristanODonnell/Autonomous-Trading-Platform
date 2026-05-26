# Diagnostics CLI Domain Audit

Target CLI domain: `diagnostics`
Target CLI file: `src/autonomous_trading_platform/cli/commands/diagnostics.py`

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `diagnostics snapshot` | `--json` | `handle_snapshot` | no intended mutation | conditional | conditional/no | `BROKER_OR_EXTERNAL` |

Notes:

- The command captures current platform state through `RuntimeSnapshotService.capture()`.
- It reads operator controls, persisted operator settings, strategy controls, allocation state, dataset versions, audit/recent activity, experiments, portfolio summary/holdings/allocation/performance/risk.
- It does not intentionally write DB state.
- It may call Alpaca broker APIs because `RuntimeSnapshotService` tries to construct `AlpacaPortfolioService` and use broker-backed portfolio summary, holdings, and allocation before falling back to local DB-backed services.
- It catches many service-level exceptions and degrades sections to `None` or empty lists, which is useful for debugging but can hide wiring failures.

## 2. Domain Responsibility Check

| Command | Classification | Rationale |
|---|---|---|
| `diagnostics snapshot` | correctly placed, but should be made local-only by default | Diagnostics is defined as current state snapshots and read-only debugging. The command fits, but broker-backed portfolio calls make it less safe than a pure local diagnostic snapshot. |

Related placement notes:

- Portfolio-specific slices should be duplicated or wrapped in `portfolio` later.
- Controls/settings/risk sections are acceptable inside a broad diagnostic snapshot, but their owner domains should also expose focused read commands.
- Broker-backed live portfolio inspection belongs more naturally in `execution` or `portfolio`, not default diagnostics.

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Classification | Implementation target service/function | Priority |
|---|---|---|---|---|---|
| `diagnostics snapshot --local-only` | Capture state using only local DB/services; never construct broker clients. | Diagnostics must be safe for local read-only debugging. | read-only | `RuntimeSnapshotService` option to disable `AlpacaPortfolioService` | P0 |
| `diagnostics snapshot --output <path>` | Save the snapshot JSON to an artifact file. | Snapshots are debugging artifacts. | local artifact output | existing `RuntimeSnapshot.model_dump(mode="json")` | P0 |
| `diagnostics snapshot --section <name>` | Capture one section: controls, settings, portfolio, allocations, datasets, experiments, activity. | Faster targeted debugging with less failure surface. | read-only | section methods in `RuntimeSnapshotService` or extracted services | P1 |
| `diagnostics controls` | Print current controls state without mutation. | Read-only debugging of pause/kill switch/trading mode. | read-only, cross-domain state | `RuntimeControlService.get_controls_state` | P1 |
| `diagnostics settings` | Print persisted operator settings. | Debugging the effective runtime settings source of truth. | read-only | `OperatorSettingsService.get_settings` | P1 |
| `diagnostics datasets` | Show latest raw/adjusted/features/feature version records. | Data lineage freshness is common debugging context. | read-only | `DatasetVersionsRepository.get_latest_validated`, `FeatureDatasetVersions` query | P1 |
| `diagnostics activity --limit <n>` | Show recent audit/activity feed. | Read-only debugging timeline. | read-only | `RecentActivityService.list_recent_activity` or `AuditLogService.list_events` | P1 |
| `diagnostics portfolio --local-only` | Local DB-backed portfolio summary/holdings/performance/risk. | Debugging portfolio state without broker dependency. | read-only | `PortfolioSummaryService`, `PortfolioAnalyticsService` | P1 |
| `diagnostics experiments --limit <n>` | Show recent experiment statuses and pass counts. | Useful debugging of research-to-runtime state. | read-only | `ExperimentCatalogService.list_experiments` | P2 |
| `diagnostics runtime-jobs --limit <n>` | Show recent runtime job evidence without operational verification semantics. | Diagnostics can inspect, operations verifies. | read-only | `OperationsService.list_jobs`, `OperationsService.list_job_runs` | P2 |
| `diagnostics recent-errors --limit <n>` | Show failed manifests/jobs and recent failure audit events. | Quick read-only failure debugging. | read-only | `RunManifestRepository.list_failed_runs`, `OperationsRepository`, `AuditLogService` | P2 |

## 4. Testing Plan

Phase 0: `--help` commands

```powershell
atp diagnostics --help
atp diagnostics snapshot --help
```

Phase 1: safe read-only commands

```powershell
$env:APP_ENV="test"
$env:DATABASE_URL="sqlite:///:memory:"
$env:TRADING_ENVIRONMENT="paper"
$env:NO_LIVE_TRADING="true"
$env:VALIDATE_BROKER_CONFIG="false"

atp diagnostics snapshot --json
atp diagnostics snapshot
```

Preferred after adding a local-only guard:

```powershell
atp diagnostics snapshot --local-only --json
atp diagnostics snapshot --local-only --output artifacts/diagnostics/runtime-snapshot.json
```

Phase 2: local DB mutation commands

```powershell
# None currently registered.
```

Phase 3: cross-domain/runtime commands

```powershell
# Seed or run runtime state first, then inspect it.
atp runtime trigger-job --job-name trading_cycle
atp diagnostics snapshot --json
```

Phase 4: broker/external commands if applicable

```powershell
$env:TRADING_ENVIRONMENT="paper"
$env:PAPER_BROKER_API_KEY="<paper-key>"
$env:PAPER_BROKER_API_SECRET="<paper-secret>"
$env:VALIDATE_BROKER_CONFIG="true"

atp diagnostics snapshot --json
```

The Phase 4 behavior should become explicit, for example:

```powershell
atp diagnostics snapshot --include-broker --json
```

## 5. Risks / Suspicious Wiring

- No handler signature mismatches found.
- No parser-required handler inputs are missing.
- No obvious placeholder command.
- `diagnostics snapshot` is named like a local read-only snapshot, but it can attempt broker-backed portfolio reads via `AlpacaPortfolioService`.
- There is no `--local-only` or `--include-broker` flag, so external broker access is implicit.
- There is no `--output` option for saving a snapshot artifact.
- There is no section filter, so a portfolio/broker issue can affect the broad snapshot path even when the operator only wants controls or datasets.
- `build_dependencies()` constructs full `Settings()` even though the command mostly needs DB access. Missing broker/config env can block a local diagnostic.
- `RuntimeSnapshotService` swallows broad exceptions and returns missing sections. This keeps the CLI resilient but can hide broken repository/service wiring unless JSON consumers inspect absent sections.
- Human-readable output in `diagnostics.py` includes mojibake/non-ASCII rendering artifacts in symbols such as arrows, triangles, blocks, and warning marks. This is not a behavioral bug, but it can make terminal output unprofessional or hard to scan.
- The current diagnostics file has no dedicated CLI tests visible in `tests/cli/commands`; coverage appears indirect through service/API tests.
- No audit logging is needed because the command should remain read-only.
- No dry-run is needed for current behavior if broker access is made opt-in and DB writes remain absent.

## 6. Recommended Refactor / Extension

Keep `diagnostics snapshot`, but make it safer and more explicit:

- Add `--local-only` as the default behavior or add `--include-broker` for broker-backed portfolio reads.
- Add `--output <path>` for JSON artifact capture.
- Add `--section` to reduce cross-domain blast radius during debugging.
- Add focused read-only wrappers for controls, settings, datasets, activity, portfolio, and recent errors.
- Keep mutation, operational verification, and health checks out of diagnostics; route those to `controls`, `settings`, `operations`, `runtime`, or `platform`.
- Add CLI parser/handler tests for `diagnostics snapshot`, especially JSON mode and degraded/missing-section behavior.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `diagnostics snapshot` | Functional broad runtime snapshot with human/JSON output | yes | Medium: implicit broker calls, broad exception swallowing, no artifact output | Keep; add local-only/default no-broker behavior, explicit broker opt-in, output file support, section filters, and CLI tests |
