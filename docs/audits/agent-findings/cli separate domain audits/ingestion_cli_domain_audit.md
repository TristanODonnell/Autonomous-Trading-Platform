# Ingestion CLI Domain Audit

Target CLI domain: `ingestion`
Target CLI file: `src/autonomous_trading_platform/cli/commands/ingestion.py`

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `ingestion run-bars` | `--timestamp` optional | `handle_run_bars` | yes | yes | no | `BROKER_OR_EXTERNAL` |
| `ingestion run-backfill` | `--symbols` required, `--start` required, `--end` required | `handle_run_backfill` | yes | yes | no | `BROKER_OR_EXTERNAL` |
| `ingestion run-corporate-actions` | none | `handle_run_corporate_actions` | yes | yes | no | `BROKER_OR_EXTERNAL` |
| `ingestion inspect-bar` | `--symbol` required, `--timestamp` required | `handle_inspect_bar` | no intended mutation; opens/commits UOW transaction | no | yes | `READ_ONLY_SAFE` |

Notes:

- `run-bars` runs `run_market_ingestion_cycle`, resolves active universe symbols unless overridden internally, fetches Alpaca market bars, writes market bars/parquet, dataset versions, ingestion runs, runtime job runs, manifests, checkpoints, quality incidents, metrics, traces, and audit logs.
- `run-backfill` runs `run_market_backfill_cycle`, fetches historical Alpaca bars for user-supplied symbols/date range, writes parquet chunks, dataset versions, ingestion runs, runtime job runs, manifests, checkpoints, quality records, metrics, traces, and audit logs.
- `run-corporate-actions` runs `run_corporate_action_ingestion_cycle`, fetches Alpaca corporate actions, reads raw bars, writes corporate action records, adjusted bars/parquet, dataset versions, ingestion runs, runtime job runs, manifests, metrics, traces, and audit logs.
- `inspect-bar` is local DB inspection only, but it uses `SorUnitOfWork`, which starts and commits a transaction even though it only reads.

## 2. Domain Responsibility Check

| Command | Classification | Rationale |
|---|---|---|
| `ingestion run-bars` | correctly placed | Market data ingestion is core ingestion-domain behavior. |
| `ingestion run-backfill` | correctly placed | Historical market data backfill belongs in ingestion. |
| `ingestion run-corporate-actions` | correctly placed | Corporate action ingestion and adjusted data generation belong in ingestion. |
| `ingestion inspect-bar` | correctly placed; could be duplicated/wrapped in diagnostics | Inspecting stored market bars is ingestion debugging, but a read-only diagnostic wrapper would also be useful. |

Related placement notes:

- Runtime may wrap these through `runtime trigger-job`, but ownership of the actual ingestion commands should remain in `ingestion`.
- Platform may compose these in realistic end-to-end workflows, but should not own the primitive ingestion commands.
- Metadata mutation endpoints in REST are adjacent but not currently represented in the ingestion CLI.

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Classification | Implementation target service/function | Priority |
|---|---|---|---|---|---|
| `ingestion plan-bars --timestamp <iso>` | Resolve cycle window, active universe symbols, expected dataset version, and prerequisites without fetching/writing. | Operators need a safe preflight before live data fetch. | read-only | `UniverseMembershipService`, `DailyDatasetVersionResolverService` read path | P0 |
| `ingestion run-bars --dry-run` | Validate prerequisites and show intended ingestion without DB/parquet writes or external fetch. | Ingestion run command needs safe test mode. | read-only | `run_market_ingestion_cycle` dry-run option or planning service | P0 |
| `ingestion run-bars --symbols SPY,AAPL` | Allow controlled manual symbol override from CLI. | The cycle already supports `symbols_override`; CLI does not expose it. | broker-facing mutation | `run_market_ingestion_cycle(symbols_override=...)` | P1 |
| `ingestion run-backfill --dry-run` | Validate symbols/date range and estimate chunks/trading sessions without Alpaca fetch or writes. | Backfills can be large and expensive. | read-only | `MarketBackfillService` planning helper | P0 |
| `ingestion run-backfill --max-days <n>` or built-in date guard | Bound accidental large historical requests. | Protects broker quota and local storage. | broker-facing guard | CLI validation before `run_market_backfill_cycle` | P0 |
| `ingestion run-corporate-actions --dry-run` | Validate latest raw bars dataset and show intended corporate-action cycle without fetch/writes. | Corporate actions mutate multiple stores and adjusted datasets. | read-only | `run_corporate_action_ingestion_cycle` planning path | P0 |
| `ingestion run-corporate-actions --source-raw-bars-dataset-version <id>` | Expose existing cycle parameter for deterministic adjusted-data generation. | Corporate action ingestion already supports source dataset selection internally. | broker-facing mutation | `run_corporate_action_ingestion_cycle(source_raw_bars_dataset_version_id=...)` | P1 |
| `ingestion inspect-ingestion-run --ingestion-run-id <id>` | Inspect one ingestion run status, timestamps, error, dataset version. | Ingestion run lifecycle is core domain state. | read-only | `IngestionRunsRepository.get_by_ingestion_run_id` | P0 |
| `ingestion list-ingestion-runs --limit <n> [--status <status>]` | List recent ingestion runs. | Makes ingestion lifecycle operable from CLI. | read-only | `IngestionRunsRepository` query | P0 |
| `ingestion inspect-dataset-version --dataset-version-id <id>` | Inspect dataset version metadata, validation status, coverage. | Dataset lineage is ingestion-owned. | read-only | `DatasetVersionsRepository` | P1 |
| `ingestion latest-dataset --dataset-name raw_bars --price-basis raw` | Show latest validated dataset version. | Useful readiness/debugging command. | read-only | `DatasetVersionsRepository.get_latest_validated` | P1 |
| `ingestion inspect-checkpoint --ingestion-run-id <id> [--symbol <symbol>]` | Inspect incremental/backfill checkpoints and failures. | Checkpoints are ingestion recovery state. | read-only | `IngestionCheckpointsRepository` | P1 |
| `ingestion list-incidents --dataset-version <id> [--symbol <symbol>]` | Show missing/late/outlier bar incidents. | Quality incidents are ingestion observability. | read-only | `MissingBarIncidentsRepository` | P1 |
| `ingestion inspect-coverage --dataset-version <id> --symbol <symbol>` | Inspect symbol/date coverage rows. | Coverage is ingestion quality evidence. | read-only | `SymbolDateCoverageRepository` | P2 |
| `ingestion inspect-corporate-action --symbol <symbol>` | Inspect stored corporate actions for a symbol. | Corporate action state is ingestion-owned. | read-only | `CorporateActionRepository` | P2 |
| `ingestion run-backfill --output <path>` | Emit run summary artifact with run IDs, dataset version IDs, and counts. | Backfill runs need traceable artifacts. | local artifact output | handler-level summary from cycle return value | P2 |

## 4. Testing Plan

Phase 0: `--help` commands

```powershell
atp ingestion --help
atp ingestion run-bars --help
atp ingestion run-backfill --help
atp ingestion run-corporate-actions --help
atp ingestion inspect-bar --help
```

Phase 1: safe read-only commands

```powershell
$env:APP_ENV="test"
$env:DATABASE_URL="sqlite:///:memory:"
$env:TRADING_ENVIRONMENT="paper"
$env:NO_LIVE_TRADING="true"
$env:VALIDATE_BROKER_CONFIG="false"

atp ingestion inspect-bar --symbol AAPL --timestamp 2026-05-08T12:00:00Z
```

Proposed read-only commands after extension:

```powershell
atp ingestion plan-bars --timestamp 2026-05-08T12:00:00Z
atp ingestion list-ingestion-runs --limit 20
atp ingestion latest-dataset --dataset-name raw_bars --price-basis raw
atp ingestion list-incidents --dataset-version raw_bars_20260508
```

Phase 2: local DB mutation commands

```powershell
# None currently registered that avoid external APIs.
# Proposed metadata-only lifecycle commands could live here if added:
atp ingestion mark-ingestion-run-failed --ingestion-run-id <run-id> --error-message "manual test failure"
```

Phase 3: cross-domain/runtime commands

```powershell
# Requires active universe state and DB.
atp universe inspect-ingestion-input --timestamp 2026-05-08T12:00:00Z
atp ingestion run-bars --timestamp 2026-05-08T12:00:00Z
```

Phase 4: broker/external commands

```powershell
$env:APP_ENV="local"
$env:DATABASE_URL="postgresql+psycopg://ratp:ratp@localhost:5432/ratp"
$env:TRADING_ENVIRONMENT="paper"
$env:PAPER_BROKER_API_KEY="<paper-key>"
$env:PAPER_BROKER_API_SECRET="<paper-secret>"
$env:NO_LIVE_TRADING="true"

atp ingestion run-bars --timestamp 2026-05-08T12:00:00Z

atp ingestion run-backfill `
  --symbols SPY,AAPL,MSFT `
  --start 2026-05-01T13:30:00Z `
  --end 2026-05-01T20:00:00Z

atp ingestion run-corporate-actions
```

## 5. Risks / Suspicious Wiring

- No handler signature mismatches found.
- No parser-required handler inputs are missing for the current handlers.
- No obvious placeholder commands.
- `run-bars`, `run-backfill`, and `run-corporate-actions` mutate DB/parquet and call Alpaca without `--dry-run`, `--confirm`, or a visible preflight plan.
- `run-backfill` lacks date-order validation; `--end` before `--start` is not rejected at the CLI boundary.
- `run-backfill` lacks range-size and symbol-count guards, so a manual command can request a very large historical fetch.
- `run-backfill` requires symbols, but `_parse_symbols` only normalizes non-empty values; it does not validate symbol format or deduplicate.
- `run-bars` does not expose the cycle's existing `symbols_override`, which makes controlled small manual ingestion harder.
- `run-corporate-actions` does not expose the cycle's existing `source_raw_bars_dataset_version_id`, limiting deterministic replay/debugging.
- `run-corporate-actions` has no timestamp/window option; it always uses current UTC time inside the cycle.
- `inspect-bar` uses `SorUnitOfWork`, which commits on exit even though it only reads.
- Handler success output is thin: run commands print `"success"` but do not return run IDs, dataset version IDs, ingestion run IDs, runtime job IDs, row counts, or artifact paths.
- Run commands should emit artifacts or at least structured run summaries for automation and debugging.
- External API usage is not clearly guarded by paper/live data mode or quota/range checks. This is market data rather than order submission, but it still has cost/quota and reproducibility risk.
- Mutation paths do have audit/runtime logging inside cycles, which is good; the CLI itself does not add operator identity or invocation metadata.

## 6. Recommended Refactor / Extension

Keep the ingestion domain and keep all four current commands, but harden the operator surface:

- Add `--dry-run` or separate `plan-*` commands for bars, backfill, and corporate actions.
- Add argument validation for date ordering, max range, max symbols, and duplicate symbols.
- Add JSON/artifact output containing run ID, ingestion run ID, dataset version ID, runtime job ID, status, counts, and paths.
- Add `--symbols` to `run-bars` and `--source-raw-bars-dataset-version` to `run-corporate-actions`.
- Add read-only inspection commands for ingestion runs, dataset versions, checkpoints, incidents, coverage, and corporate actions.
- Replace read-only `inspect-bar` UOW usage with a simple session/repository read that does not commit.
- Add optional `--actor` or invocation metadata for manual mutation commands so audit trails distinguish scheduler runs from operator-triggered CLI runs.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `ingestion run-bars` | Functional market ingestion cycle runner | yes | High: external fetch and persistence without dry-run/summary detail | Keep; add plan/dry-run, optional symbols, richer output |
| `ingestion run-backfill` | Functional historical backfill runner | yes | High: external fetch, large-range risk, no dry-run/range guard | Keep; add validation, dry-run, range limits, artifact output |
| `ingestion run-corporate-actions` | Functional corporate action ingestion runner | yes | Medium: external fetch and adjusted-data mutation with few CLI controls | Keep; add dry-run, source dataset override, artifact output |
| `ingestion inspect-bar` | Functional stored bar lookup | yes | Low: read-only intent but commits UOW transaction | Keep; switch to non-mutating read path and add related inspection commands |
