# Universe CLI Domain Audit

Target CLI domain: `universe`
Target CLI file: `src/autonomous_trading_platform/cli/commands/universe.py`

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `universe select-now` | `--timestamp` | `handle_select_now` | yes intended | no | no | `SUSPICIOUS` |
| `universe inspect-active` | `--timestamp` | `handle_inspect_active` | no | no | yes | `READ_ONLY_SAFE` |
| `universe inspect-symbols` | `--timestamp` | `handle_inspect_symbols` | no | no | yes | `READ_ONLY_SAFE` |
| `universe inspect-symbol` | `--symbol`, `--timestamp` | `handle_inspect_symbol` | no | no | yes | `READ_ONLY_SAFE` |
| `universe validate-active` | `--timestamp` | `handle_validate_active` | no | no | yes | `READ_ONLY_SAFE` |
| `universe validation-report` | `--timestamp` | `handle_validation_report` | no | no | yes | `READ_ONLY_SAFE` |
| `universe inspect-ingestion-input` | `--timestamp` | `handle_inspect_ingestion_input` | no | no | yes | `READ_ONLY_SAFE` |
| `universe seed` | `--symbols`, `--timestamp`, `--source`, `--name` | `handle_seed` | yes | no | no | `LOCAL_DB_MUTATION` |
| `universe raw-pool-refresh` | `--timestamp`, `--cadence`, `--force` | `handle_raw_pool_refresh` | yes | yes | no | `BROKER_OR_EXTERNAL` |
| `universe raw-pool-inspect` | `--timestamp`, `--asset-type`, `--exchange` | `handle_raw_pool_inspect` | no | no | yes | `READ_ONLY_SAFE` |
| `universe raw-pool-inspect-symbol` | `--symbol`, `--timestamp` | `handle_raw_pool_inspect_symbol` | no | no | yes | `READ_ONLY_SAFE` |
| `universe candidate-generate` | `--timestamp`, `--lookback-days`, `--min-price`, `--min-addv`, `--max-symbols`, `--name` | `handle_candidate_generate` | yes | no | no | `LOCAL_DB_MUTATION` |
| `universe candidate-inspect` | `--version-id`, `--timestamp` | `handle_candidate_inspect` | no | no | yes | `READ_ONLY_SAFE` |
| `universe candidate-inspect-rejections` | `--version-id`, `--reason` | `handle_candidate_inspect_rejections` | no | no | yes | `READ_ONLY_SAFE` |
| `universe candidate-inspect-symbol` | `--symbol`, `--version-id` | `handle_candidate_inspect_symbol` | no | no | yes | `READ_ONLY_SAFE` |
| `universe history` | `--limit`, `--status` | `handle_history` | no | no | yes | `READ_ONLY_SAFE` |
| `universe runtime-status` | `--timestamp` | `handle_runtime_status` | no | no | yes | `READ_ONLY_SAFE` |
| `universe observability-status` | `--timestamp` | `handle_observability_status` | no | no | yes | `READ_ONLY_SAFE` |
| `universe propose-rebalance` | `--candidate-version-id`, `--active-version-id`, `--timestamp`, `--target-size`, `--max-churn-pct`, `--retain-threshold`, `--add-threshold`, `--force`, `--dry-run`, `--name` | `handle_propose_rebalance` | conditional | no | only with `--dry-run` | `LOCAL_DB_MUTATION` |
| `universe rotate` | `--candidate-version-id`, `--timestamp`, `--target-size`, `--max-churn-pct`, `--retain-threshold`, `--add-threshold`, `--rotation-reason`, `--force`, `--skip-cadence-check`, `--dry-run`, `--approved-by` | `handle_rotate` | conditional | no | only with `--dry-run` | `LOCAL_DB_MUTATION` |
| `universe rollback` | `--target-version-id`, `--reason`, `--approved-by`, `--timestamp`, `--dry-run` | `handle_rollback` | conditional | no | only with `--dry-run` | `LOCAL_DB_MUTATION` |
| `universe rotation-history` | `--limit` | `handle_rotation_history` | no | no | yes | `READ_ONLY_SAFE` |
| `universe rebalance-history` | `--limit` | `handle_rebalance_history` | no | no | yes | `READ_ONLY_SAFE` |
| `universe rotation-status` | none | `handle_rotation_status` | no | no | yes | `READ_ONLY_SAFE` |
| `universe history-for-date` | `--timestamp` | `handle_history_for_date` | no | no | yes | `READ_ONLY_SAFE` |
| `universe replay-timeline` | `--start`, `--end` | `handle_replay_timeline` | no | no | yes | `READ_ONLY_SAFE` |
| `universe compare-universes` | `--version-a`, `--version-b` | `handle_compare_universes` | no | no | yes | `READ_ONLY_SAFE` |
| `universe symbol-history` | `--symbol`, `--start`, `--end` | `handle_symbol_history` | no | no | yes | `READ_ONLY_SAFE` |

Notes:

- This domain is broad and mostly well covered. It includes current-state inspection, raw pool lifecycle, candidate lifecycle, active universe validation, rebalance proposal, rotation, rollback, history, replay support, comparison, and symbol lineage.
- `raw-pool-refresh` is the only clearly broker-facing command; it constructs an Alpaca `TradingClient` and calls `get_all_assets()`.
- Several mutation helpers commit and/or rollback in jobs, but some sessions are not closed by the job helpers.
- `select-now` is suspicious: `run_universe_selection_cycle()` uses `get_session()` and mutates ORM rows, but the function shown does not commit or close the session despite `autocommit=False`.

## 2. Domain Responsibility Check

| Command | Classification | Rationale |
|---|---|---|
| `universe select-now` | correctly placed, but suspicious implementation | Universe selection belongs here, but the job transaction handling needs review. |
| `universe inspect-active` | correctly placed | Active universe inspection is core universe behavior. |
| `universe inspect-symbols` | correctly placed | Symbol membership inspection is core universe behavior. |
| `universe inspect-symbol` | correctly placed | Per-symbol membership inspection is core universe behavior. |
| `universe validate-active` | correctly placed | Universe validation belongs here. |
| `universe validation-report` | correctly placed, possible duplicate | Currently just wraps `validate-active`; acceptable alias, but it could grow into richer reporting. |
| `universe inspect-ingestion-input` | correctly placed; duplicated/wrapped elsewhere optional | This is a universe-owned symbol set consumed by ingestion. A read-only ingestion wrapper could call it later. |
| `universe seed` | correctly placed | Explicit universe creation is universe-owned. |
| `universe raw-pool-refresh` | correctly placed | Raw tradable-symbol pool is universe source data. |
| `universe raw-pool-inspect` | correctly placed | Raw pool inspection is universe-owned. |
| `universe raw-pool-inspect-symbol` | correctly placed | Raw pool per-symbol inspection is universe-owned. |
| `universe candidate-generate` | correctly placed | Candidate universe generation belongs here. |
| `universe candidate-inspect` | correctly placed | Candidate inspection belongs here. |
| `universe candidate-inspect-rejections` | correctly placed | Candidate rejection explainability belongs here. |
| `universe candidate-inspect-symbol` | correctly placed | Candidate symbol explainability belongs here. |
| `universe history` | correctly placed | Version lifecycle history belongs here. |
| `universe runtime-status` | should be duplicated/wrapped elsewhere | Universe owns the data; `runtime` or `diagnostics` may wrap this readiness view. |
| `universe observability-status` | should be duplicated/wrapped elsewhere | Useful here, but `operations` or `diagnostics` could expose aggregate observability. |
| `universe propose-rebalance` | correctly placed | Universe rebalance proposal is core universe lifecycle behavior. |
| `universe rotate` | correctly placed | Universe rotation is core universe lifecycle behavior. |
| `universe rollback` | correctly placed | Universe rollback is core universe lifecycle behavior. |
| `universe rotation-history` | correctly placed | Rotation audit/history belongs here. |
| `universe rebalance-history` | correctly placed | Rebalance audit/history belongs here. |
| `universe rotation-status` | correctly placed | Latest rotation status belongs here. |
| `universe history-for-date` | correctly placed | Historical as-of membership belongs here. |
| `universe replay-timeline` | correctly placed; duplicated/wrapped elsewhere optional | Universe owns membership transitions; `runtime` replay could wrap it. |
| `universe compare-universes` | correctly placed | Version comparison belongs here. |
| `universe symbol-history` | correctly placed | Symbol membership lineage belongs here. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Classification | Implementation target service/function | Priority |
|---|---|---|---|---|---|
| `universe inspect-version --version-id <id>` | Inspect any version, not only active/latest candidate. | Direct version inspection is core universe operability. | read-only | `UniverseVersionRepository.get_by_version_id`, `get_members` | P0 |
| `universe validate-version --version-id <id>` | Validate candidate/proposed/retired version by ID. | Validation should not be limited to active version. | read-only | `UniverseValidationService.validate_version_row` | P0 |
| `universe select-now --dry-run` | Compute intended selection without persisting/activating. | Selection can retire/activate universes; safe preview is important. | read-only | `run_universe_selection_cycle` planning/dry-run path | P0 |
| `universe candidate-generate --dry-run` | Score and summarize a candidate without inserting rows. | Candidate generation can be expensive and persistent. | read-only | `UniverseCandidateBuilder.build_candidate` without repository insert | P0 |
| `universe seed --dry-run` | Validate explicit symbols and show resulting version metadata without activation. | Manual seed is high-impact. | read-only | `UniverseVersionService.build_version`, `UniverseValidationService` | P1 |
| `universe raw-pool-refresh --dry-run` | Fetch/compare broker symbol pool without persisting new snapshot. | Broker refresh mutates raw pool and has external dependency. | broker-facing, no local mutation | `RawMarketPoolRefreshService.refresh` dry-run/preview path | P1 |
| `universe raw-pool-history --limit <n>` | List raw market pool snapshots. | Raw pool lifecycle is part of universe source lineage. | read-only | `RawMarketPoolRepository` | P1 |
| `universe raw-pool-inspect --snapshot-id <id>` | Inspect a specific raw pool snapshot, not only as-of latest. | Needed for reproducibility/debugging. | read-only | `RawMarketPoolRepository.get_snapshot_by_id` | P1 |
| `universe export-version --version-id <id> --output <path>` | Emit full version + members + metadata artifact. | Universe membership is a replay/research artifact. | local artifact output | `UniverseVersionRepository`, JSON writer | P1 |
| `universe import-version --input <path> --dry-run` | Validate and optionally seed from an exported version. | Useful for environment portability. | local-mutating when not dry-run | `UniverseVersionService`, `UniverseValidationService` | P2 |
| `universe lifecycle-symbol --symbol <symbol>` | Show ticker lifecycle events alongside membership history. | Ticker lifecycle affects universe eligibility. | read-only | `TickerLifecycleService`, `TickerLifecycleRepository` | P2 |
| `universe inspect-rebalance --rebalance-run-id <id>` | Inspect one rebalance run in full. | Current history only lists recent summaries. | read-only | `UniverseRebalanceRepository` | P2 |
| `universe inspect-rotation --rotation-id <id>` | Inspect one rotation/rollback record in full. | Current status/history are summary-oriented. | read-only | `UniverseRotationRepository` | P2 |

## 4. Testing Plan

Phase 0: `--help` commands

```powershell
atp universe --help
atp universe select-now --help
atp universe inspect-active --help
atp universe inspect-symbols --help
atp universe inspect-symbol --help
atp universe validate-active --help
atp universe validation-report --help
atp universe inspect-ingestion-input --help
atp universe seed --help
atp universe raw-pool-refresh --help
atp universe raw-pool-inspect --help
atp universe raw-pool-inspect-symbol --help
atp universe candidate-generate --help
atp universe candidate-inspect --help
atp universe candidate-inspect-rejections --help
atp universe candidate-inspect-symbol --help
atp universe history --help
atp universe runtime-status --help
atp universe observability-status --help
atp universe propose-rebalance --help
atp universe rotate --help
atp universe rollback --help
atp universe rotation-history --help
atp universe rebalance-history --help
atp universe rotation-status --help
atp universe history-for-date --help
atp universe replay-timeline --help
atp universe compare-universes --help
atp universe symbol-history --help
```

Phase 1: safe read-only commands

```powershell
$env:APP_ENV="test"
$env:DATABASE_URL="sqlite:///:memory:"
$env:TRADING_ENVIRONMENT="paper"
$env:NO_LIVE_TRADING="true"
$env:VALIDATE_BROKER_CONFIG="false"

atp universe inspect-active --timestamp 2026-05-08T12:00:00Z
atp universe inspect-symbols --timestamp 2026-05-08T12:00:00Z
atp universe inspect-symbol --symbol AAPL --timestamp 2026-05-08T12:00:00Z
atp universe validate-active --timestamp 2026-05-08T12:00:00Z
atp universe inspect-ingestion-input --timestamp 2026-05-08T12:00:00Z
atp universe raw-pool-inspect --timestamp 2026-05-08T12:00:00Z --asset-type us_equity
atp universe raw-pool-inspect-symbol --symbol AAPL --timestamp 2026-05-08T12:00:00Z
atp universe candidate-inspect
atp universe history --limit 10
atp universe runtime-status --timestamp 2026-05-08T12:00:00Z
atp universe observability-status --timestamp 2026-05-08T12:00:00Z
atp universe rotation-history --limit 10
atp universe rebalance-history --limit 10
atp universe rotation-status
atp universe history-for-date --timestamp 2026-05-08T12:00:00Z
atp universe replay-timeline --start 2026-05-01T00:00:00Z --end 2026-05-08T00:00:00Z
atp universe symbol-history --symbol AAPL --start 2026-05-01T00:00:00Z --end 2026-05-08T00:00:00Z
```

Phase 2: local DB mutation commands

```powershell
atp universe seed `
  --symbols SPY,AAPL,MSFT,NVDA `
  --timestamp 2026-05-08T00:00:00Z `
  --source custom `
  --name manual_test_universe

atp universe candidate-generate `
  --timestamp 2026-05-08T20:00:00Z `
  --lookback-days 20 `
  --min-price 1.00 `
  --min-addv 5000000 `
  --max-symbols 100 `
  --name candidate_test_2026_05_08

atp universe propose-rebalance `
  --timestamp 2026-05-08T20:00:00Z `
  --target-size 100 `
  --max-churn-pct 0.15 `
  --dry-run

atp universe rotate `
  --timestamp 2026-05-08T20:00:00Z `
  --target-size 100 `
  --max-churn-pct 0.15 `
  --skip-cadence-check `
  --dry-run `
  --approved-by operator@example.com
```

Phase 3: cross-domain/runtime commands

```powershell
atp universe runtime-status --timestamp 2026-05-08T12:00:00Z
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

atp universe raw-pool-refresh --timestamp 2026-05-08T12:00:00Z --cadence daily --force
atp universe raw-pool-inspect --timestamp 2026-05-08T12:01:00Z --asset-type us_equity
```

## 5. Risks / Suspicious Wiring

- No parser/handler signature mismatches found.
- No registered parser appears to omit a required handler input.
- No obvious placeholder commands; this domain is feature-complete relative to most surrounding CLI domains.
- `select-now` is suspicious: `run_universe_selection_cycle()` mutates through a SQLAlchemy session but does not visibly commit or close the session. Since `get_session()` uses `autocommit=False`, this may not persist as advertised.
- `run_universe_selection_cycle()` also lacks a `finally: session.close()` path.
- `raw-pool-refresh`, `candidate-generate`, and `run_rebalance()` commit but do not visibly close their sessions.
- Several read handlers call `build_dependencies()` without closing the session (`inspect-active`, `inspect-symbols`, `inspect-symbol`, `inspect-ingestion-input`).
- `history` computes a timestamp even though the parser has no `--timestamp`; harmless but dead wiring.
- `candidate-inspect` defines `--timestamp` but the handler does not use it.
- `validation-report` is currently just an alias to `validate-active`; the name implies a richer operational report than it emits.
- `raw-pool-refresh` calls Alpaca without a `--dry-run` or preview mode. `--force` bypasses calendar gating, but there is no explicit confirmation for a broker-backed mutation.
- `seed` has no `--dry-run` even though it retires the current active universe and activates the new seeded version.
- `candidate-generate` has no `--dry-run`; it always persists a candidate and all included/excluded members.
- `propose-rebalance`, `rotate`, and `rollback` do have `--dry-run`, which is good.
- Numeric inputs lack range validation in argparse: `--limit`, `--target-size`, `--max-churn-pct`, `--lookback-days`, `--max-symbols`, thresholds, `--min-price`, and `--min-addv`.
- `compare-universes`, history commands, candidate inspection, and raw pool inspection emit previews but not full artifact output. That is sane for terminal use, but replay/research workflows would benefit from `--output`.
- Mutation commands mostly write lifecycle/rotation/rebalance records, but manual operator identity is optional or absent on some high-impact commands (`seed`, `candidate-generate`, `propose-rebalance`).

## 6. Recommended Refactor / Extension

Keep the universe domain intact. It is one of the more complete CLI domains.

Recommended hardening:

- Fix transaction/session handling for `select-now` and close sessions consistently in jobs and handlers.
- Add `--dry-run` to `seed`, `select-now`, `candidate-generate`, and `raw-pool-refresh`.
- Add range validation for limits, thresholds, churn percentage, target size, lookback days, and decimal filters.
- Add `--output <path>` to inspection/comparison/history commands that currently emit previews.
- Add focused `inspect-version` and `validate-version` commands.
- Add raw pool snapshot history and inspect-by-id commands.
- Require or strongly encourage `--approved-by` / `--actor` on high-impact mutation commands.
- Keep cross-domain wrappers optional: `runtime` can wrap readiness, `ingestion` can wrap ingestion-input, and `operations` can wrap observability-status, but universe should remain the source command owner.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `universe select-now` | Intended selection/activation runner | yes | High: suspected missing commit/session close | Fix transaction handling; add dry-run |
| `universe inspect-active` | Functional active version inspection | yes | Low: session close inconsistency | Keep; close session consistently |
| `universe inspect-symbols` | Functional active symbol listing | yes | Low: session close inconsistency | Keep; close session consistently |
| `universe inspect-symbol` | Functional symbol membership check | yes | Low: session close inconsistency | Keep; close session consistently |
| `universe validate-active` | Functional active validation | yes | Low | Keep |
| `universe validation-report` | Alias for validation | yes | Low: name overpromises | Expand report or rename alias |
| `universe inspect-ingestion-input` | Functional ingestion symbol preview | yes | Low: session close inconsistency | Keep; optional ingestion wrapper |
| `universe seed` | Functional manual activation path | yes | Medium: no dry-run/actor requirement | Add dry-run and actor/approval metadata |
| `universe raw-pool-refresh` | Functional broker-backed raw pool refresh | yes | Medium: external call and mutation without dry-run | Add dry-run/preview and close session |
| `universe raw-pool-inspect` | Functional raw pool inspection | yes | Low | Keep; add snapshot-id mode |
| `universe raw-pool-inspect-symbol` | Functional raw pool symbol check | yes | Low | Keep |
| `universe candidate-generate` | Functional persisted candidate generation | yes | Medium: no dry-run; session not closed | Add dry-run and close session |
| `universe candidate-inspect` | Functional candidate summary | yes | Low: unused timestamp arg | Remove/use timestamp |
| `universe candidate-inspect-rejections` | Functional rejection explainability | yes | Low | Keep |
| `universe candidate-inspect-symbol` | Functional candidate symbol explainability | yes | Low | Keep |
| `universe history` | Functional version history | yes | Low: no limit bounds | Add bounds |
| `universe runtime-status` | Functional runtime readiness view | yes | Low | Keep; optional runtime wrapper |
| `universe observability-status` | Functional universe observability summary | yes | Low | Keep; optional operations wrapper |
| `universe propose-rebalance` | Functional proposal path with dry-run | yes | Medium when not dry-run | Keep; add stronger actor/output controls |
| `universe rotate` | Functional rotation path with dry-run | yes | Medium when not dry-run | Keep; require/encourage approved-by |
| `universe rollback` | Functional rollback path with dry-run and required reason | yes | Medium when not dry-run | Keep; require/encourage approved-by |
| `universe rotation-history` | Functional rotation history | yes | Low | Keep; add inspect-by-id |
| `universe rebalance-history` | Functional rebalance history | yes | Low | Keep; add inspect-by-id |
| `universe rotation-status` | Functional latest rotation status | yes | Low | Keep |
| `universe history-for-date` | Functional historical as-of lookup | yes | Low | Keep |
| `universe replay-timeline` | Functional replay membership timeline | yes | Low | Keep; add output artifact |
| `universe compare-universes` | Functional version comparison | yes | Low | Keep; add full output option |
| `universe symbol-history` | Functional per-symbol membership lineage | yes | Low | Keep |
