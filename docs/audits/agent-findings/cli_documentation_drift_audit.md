# CLI Documentation Drift Audit

## Executive Summary

The current CLI source exposes 85 leaf commands across 12 top-level groups:
`admin`, `backtesting`, `diagnostics`, `execution`, `features`, `ingestion`,
`operations`, `research`, `runtime`, `safety`, `strategy`, and `universe`.

The previously migrated CLI stub at `docs/backend/cli/cli.md` did not inventory
the active command surface. The detailed operator handbook at
`docs/backend/cli/runtime_harness_reference.md` is useful but stale: it reports
56 commands and significantly undercounts current `universe`, `research`,
`runtime`, and `backtesting` coverage.

Documentation updates made in this audit:

- Rebuilt `docs/backend/cli/cli.md` as the canonical CLI index.
- Added lightweight CLI coverage READMEs for meaningful CLI domains.
- Added explicit universe CLI coverage under `docs/backend/universe/README.md`.
- Marked `runtime_harness_reference.md` as a historical operator handbook whose
  command counts may lag the parser.
- Updated operations runbook/debugging indexes with current CLI links.

No application code was changed.

## Actual CLI Command Inventory

Source of truth inspected:

- `src/autonomous_trading_platform/cli/main.py`
- `src/autonomous_trading_platform/cli/commands/*.py`
- `src/autonomous_trading_platform/cli/commands/runtime_soak_loop.py`
- `pyproject.toml`

Entrypoint finding: the parser program name is `atp`, but `pyproject.toml` does
not define a `[project.scripts]` console-script alias. The code entrypoint is
`python -m autonomous_trading_platform.cli.main`.

| Command | Source | Domain | Purpose | Key Options | Notes |
|---|---|---|---|---|---|
| `safety arm-live` | `safety.py` | Safety | Arm live trading. | `--reason`, `--armed-by` | Safety-critical mutating command. |
| `safety disarm-live` | `safety.py` | Safety | Disarm live trading. | None | Safety-critical mutating command. |
| `safety enable-kill-switch` | `safety.py` | Safety | Enable kill switch. | `--reason`, `--updated-by` | Safety-critical; blocks trading. |
| `safety disable-kill-switch` | `safety.py` | Safety | Disable kill switch. | `--reason`, `--updated-by` | Safety-critical; permits trading if other gates pass. |
| `safety gate-status` | `safety.py` | Safety | Inspect gate status. | `--account-id` | Read-oriented. |
| `diagnostics snapshot` | `diagnostics.py` | Observability | Emit runtime snapshot. | `--json` | Read-oriented diagnostics. |
| `ingestion run-bars` | `ingestion.py` | Ingestion | Run bar ingestion. | `--timestamp` | Mutating ingestion path. |
| `ingestion run-backfill` | `ingestion.py` | Ingestion | Run historical backfill. | `--symbols`, `--start`, `--end` | Mutating; requires symbols/window. |
| `ingestion run-corporate-actions` | `ingestion.py` | Corporate actions | Run corporate action ingestion. | None | Mutating ingestion path. |
| `ingestion inspect-bar` | `ingestion.py` | Ingestion | Inspect one bar. | `--symbol`, `--timestamp` | Read-oriented. |
| `strategy evaluate-bar` | `strategy.py` | Research / strategy runtime | Evaluate strategy at timestamp. | `--timestamp` | Operational evaluation path. |
| `strategy inspect-readiness` | `strategy.py` | Research / strategy runtime | Inspect readiness. | `--timestamp` | Read-oriented. |
| `execution reconcile-order` | `execution.py` | Execution / broker | Reconcile one order. | `--order-id` | Mutating reconciliation. |
| `execution reconcile-open-orders` | `execution.py` | Execution / broker | Reconcile open orders. | None | Mutating reconciliation. |
| `execution inspect-order` | `execution.py` | Execution / broker | Inspect one order. | `--order-id` | Read-oriented. |
| `execution inspect-position` | `execution.py` | Execution / broker | Inspect one position. | `--symbol` | Read-oriented. |
| `execution inspect-cash` | `execution.py` | Execution / broker | Inspect cash state. | None | Read-oriented. |
| `runtime run-cycle` | `runtime.py` | Runtime | Run trading cycle. | `--timestamp` | Mutating runtime path. |
| `runtime trigger-job` | `runtime.py` | Runtime / scheduler | Trigger scheduler job. | `--job-name` | Mutating/manual job trigger. |
| `runtime inspect-manifest` | `runtime.py` | Runtime | Inspect run manifest. | `--run-id` | Read-oriented. |
| `runtime inspect-audit` | `runtime.py` | Runtime | Inspect run audit events. | `--run-id` | Read-oriented. |
| `runtime soak-loop backtest` | `runtime_soak_loop.py` | Simulation / runtime | Run backtest soak loop. | `--symbols`, `--start`, `--end`, `--initial-capital`, `--strategy-id` | Historical soak path. |
| `runtime soak-loop paper` | `runtime_soak_loop.py` | Runtime / broker | Run paper-trading soak loop. | `--mode` | Broker-facing paper path. |
| `runtime soak-loop research` | `runtime_soak_loop.py` | Research / simulation | Run research soak loop. | `--symbols`, `--start`, `--end`, `--loop`, `--experiment-plan` | Research orchestration path. |
| `runtime replay` | `runtime.py` | Runtime / simulation | Run runtime replay. | `--symbols`, `--start`, `--end`, `--starting-cash`, `--random-seed`, `--price-basis`, `--cycles`, `--output-json` | Mutates local replay state/artifacts. |
| `runtime replay-debug` | `runtime.py` | Runtime / simulation | Run debug replay. | Same replay option family. | Debug replay path. |
| `runtime replay-ingestion` | `runtime.py` | Runtime / ingestion | Replay ingestion over a window. | `--symbols`, `--start`, `--end`, `--cadence-minutes`, `--run-trading`, `--output-json` | Can run trading if `--run-trading` is set. |
| `backtesting run` | `backtesting.py` | Simulation | Run one backtest. | `--timestamp` | TODO: manually verify current implementation status. |
| `backtesting inspect-results` | `backtesting.py` | Simulation | Inspect results. | `--run-id` | Read-oriented. |
| `backtesting seed-fixture` | `backtesting.py` | Simulation | Seed fixture. | `--fixture`, `--dry-run` | Mutating unless dry-run. |
| `backtesting seed-settings` | `backtesting.py` | Simulation / governance | Seed settings. | `--config` | Mutating. |
| `backtesting read-settings` | `backtesting.py` | Simulation / governance | Read settings. | None | Read-oriented. |
| `backtesting seed-controls` | `backtesting.py` | Simulation / controls | Seed controls. | `--config`, `--clean` | Mutating. |
| `backtesting read-controls` | `backtesting.py` | Simulation / controls | Read controls. | None | Read-oriented. |
| `backtesting read-portfolio` | `backtesting.py` | Portfolio | Read portfolio state. | None | Read-oriented. |
| `backtesting read-dashboard` | `backtesting.py` | Portfolio / dashboard | Read dashboard state. | None | Read-oriented. |
| `backtesting verify-risk-parameter-effects` | `backtesting.py` | Risk / simulation | Verify risk parameter effects. | `--controls`, `--settings`, `--symbols`, `--start`, `--end`, `--starting-cash`, `--parameter` | Verification path. |
| `backtesting verify-notification-events` | `backtesting.py` | Operations / alerts | Verify notification events. | `--controls`, `--settings` | Verification path. |
| `backtesting verify-governance-allocation` | `backtesting.py` | Portfolio governance | Verify governance allocation. | `--controls`, `--settings`, `--total-capital` | Verification path. |
| `backtesting verify-auto-promotion` | `backtesting.py` | Governance | Verify auto-promotion. | `--settings` | Verification path. |
| `backtesting verify-auto-demotion` | `backtesting.py` | Governance | Verify auto-demotion. | `--settings` | Verification path. |
| `admin inspect-config` | `admin.py` | Operations | Inspect config. | None | Read-oriented. |
| `admin inspect-env` | `admin.py` | Operations | Inspect environment. | None | Read-oriented. |
| `admin inspect-failed-runs` | `admin.py` | Operations | List failed runs. | `--limit` | Read-oriented. |
| `operations verify-runtime-soak` | `operations.py` | Observability / operations | Verify runtime soak window. | `--window-start`, `--window-end`, `--stale-after-minutes` | Read/verification path. |
| `universe select-now` | `universe.py` | Universe | Run selection now. | `--timestamp` | Mutating universe state. |
| `universe inspect-active` | `universe.py` | Universe | Inspect active universe. | `--timestamp` | Read-oriented. |
| `universe inspect-symbols` | `universe.py` | Universe | List symbols. | `--timestamp` | Read-oriented. |
| `universe inspect-symbol` | `universe.py` | Universe | Inspect one symbol. | `--symbol`, `--timestamp` | Read-oriented. |
| `universe validate-active` | `universe.py` | Universe | Validate active universe. | `--timestamp` | Read/validation. |
| `universe validation-report` | `universe.py` | Universe | Emit validation report. | `--timestamp` | Read/validation. |
| `universe inspect-ingestion-input` | `universe.py` | Universe / ingestion | Inspect ingestion universe input. | `--timestamp` | Read-oriented. |
| `universe seed` | `universe.py` | Universe | Seed custom universe. | `--symbols`, `--timestamp`, `--source`, `--name` | Mutating. |
| `universe raw-pool-refresh` | `universe.py` | Universe | Refresh raw pool. | `--timestamp`, `--cadence`, `--force` | Mutating. |
| `universe raw-pool-inspect` | `universe.py` | Universe | Inspect raw pool. | `--timestamp`, `--asset-type`, `--exchange` | Read-oriented. |
| `universe raw-pool-inspect-symbol` | `universe.py` | Universe | Inspect raw-pool symbol. | `--symbol`, `--timestamp` | Read-oriented. |
| `universe candidate-generate` | `universe.py` | Universe | Generate candidate version. | `--timestamp`, `--lookback-days`, `--min-price`, `--min-addv`, `--max-symbols`, `--name` | Mutating candidate generation. |
| `universe candidate-inspect` | `universe.py` | Universe | Inspect candidate. | `--version-id`, `--timestamp` | Read-oriented. |
| `universe candidate-inspect-rejections` | `universe.py` | Universe | Inspect rejection reasons. | `--version-id`, `--reason` | Read-oriented. |
| `universe candidate-inspect-symbol` | `universe.py` | Universe | Inspect candidate symbol. | `--symbol`, `--version-id` | Read-oriented. |
| `universe history` | `universe.py` | Universe | List universe versions. | `--limit`, `--status` | Read-oriented. |
| `universe runtime-status` | `universe.py` | Universe / runtime | Inspect runtime universe status. | `--timestamp` | Read-oriented. |
| `universe observability-status` | `universe.py` | Universe / observability | Inspect observability status. | `--timestamp` | Read-oriented. |
| `universe propose-rebalance` | `universe.py` | Universe / rebalance | Propose rebalance. | `--candidate-version-id`, `--active-version-id`, `--target-size`, `--max-churn-pct`, `--force`, `--dry-run` | Mutating unless dry-run. |
| `universe rotate` | `universe.py` | Universe / rotation | Rotate universe. | `--candidate-version-id`, `--target-size`, `--max-churn-pct`, `--rotation-reason`, `--force`, `--skip-cadence-check`, `--dry-run`, `--approved-by` | Mutating unless dry-run. |
| `universe rollback` | `universe.py` | Universe / rotation | Roll back universe version. | `--target-version-id`, `--reason`, `--approved-by`, `--timestamp`, `--dry-run` | Mutating unless dry-run. |
| `universe rotation-history` | `universe.py` | Universe / rotation | List rotation history. | `--limit` | Read-oriented. |
| `universe rebalance-history` | `universe.py` | Universe / rebalance | List rebalance history. | `--limit` | Read-oriented. |
| `universe rotation-status` | `universe.py` | Universe / rotation | Inspect rotation status. | None | Read-oriented. |
| `universe history-for-date` | `universe.py` | Universe history | Resolve history at timestamp. | `--timestamp` | Read-oriented. |
| `universe replay-timeline` | `universe.py` | Universe history | Replay timeline. | `--start`, `--end` | Read-oriented. |
| `universe compare-universes` | `universe.py` | Universe history | Compare universe versions. | `--version-a`, `--version-b` | Read-oriented. |
| `universe symbol-history` | `universe.py` | Universe history | Inspect symbol membership history. | `--symbol`, `--start`, `--end` | Read-oriented. |
| `features run-pipeline` | `features.py` | Storage lineage / features | Run feature pipeline. | `--dataset-version-id`, `--price-basis`, `--symbols`, `--start-date`, `--end-date`, include flags | Mutating feature dataset path. |
| `research run-simulation` | `research.py` | Research / simulation | Run direct simulation. | `--dataset-version-id`, `--price-basis`, `--symbols`, `--start-date`, `--end-date`, `--strategy-type`, `--strategy-id`, `--random-seed` | Writes simulation/research artifacts. |
| `research run-experiment` | `research.py` | Research | Run experiment. | `--config`, inline experiment options, `--execution-mode`, `--max-workers`, `--fail-fast` | Writes experiment artifacts/state. |
| `research list-strategy-types` | `research.py` | Research | List strategy types. | `--family`, `--include-debug`, `--include-experimental`, `--format` | Read-oriented. |
| `research inspect-strategy` | `research.py` | Research | Inspect strategy. | `--strategy-type`, `--format` | Read-oriented. |
| `research list-components` | `research.py` | Research | List components. | `--component-type`, `--executable-only`, `--metadata-only`, `--format` | Read-oriented. |
| `research inspect-component` | `research.py` | Research | Inspect component. | `--component-name`, `--format` | Read-oriented. |
| `research generate-strategies` | `research.py` | Research | Generate strategy configs. | `--strategy-type`, `--family`, `--parameter-space`, `--generator`, `--n-samples`, `--output`, `--output-format` | Can write artifact with `--output`. |
| `research summarize-generated-configs` | `research.py` | Research | Summarize generated configs. | `--input`, `--format`, `--show-hashes` | Read artifact. |
| `research inspect-checkpoints` | `research.py` | Research checkpoints | Inspect checkpoint store. | `--checkpoint-store`, `--format` | Read-oriented. |
| `research plan-restart` | `research.py` | Research checkpoints | Plan restart. | `--checkpoint-store`, `--units-file`, resume/rerun flags, `--format` | Planning/read output. |
| `research resume-experiment` | `research.py` | Research checkpoints | Resume experiment units. | `--checkpoint-store`, `--units-file`, `--dry-run`, resume/rerun flags, `--format` | Parser default is dry-run; manual verification recommended before non-dry-run use. |

## Documentation Drift Findings

| Finding | Evidence | Resolution |
|---|---|---|
| `docs/backend/cli/cli.md` was only a placeholder. | It only pointed to the runtime harness and strategy-generation docs. | Rebuilt it as the canonical CLI index. |
| `runtime_harness_reference.md` command counts are stale. | It reports 56 commands; parser inspection found 85 leaf commands. | Added a status note and pointed readers to `cli.md`. |
| Universe CLI coverage was missing from the new docs tree. | `universe.py` registers 28 leaf commands; no `docs/backend/universe/` folder existed. | Added `docs/backend/universe/README.md`. |
| Domain README coverage was missing for several command groups. | CLI commands existed for runtime, safety, execution, ingestion, research, simulation, observability, storage-lineage, and portfolio-governance without lightweight CLI indexes. | Added domain README files. |
| Operations stubs did not link to concrete commands. | `docs/operations/runbooks/README.md` and `docs/operations/debugging/README.md` were draft placeholders. | Added command-link maps while keeping them as indexes. |
| Entrypoint packaging is ambiguous. | `main.py` uses parser prog `atp`, but `pyproject.toml` has no `[project.scripts]`. | Documented `python -m autonomous_trading_platform.cli.main` as the code entrypoint and noted `atp` alias ambiguity. |

## Commands Missing From Docs

Before this audit, the biggest missing or underdocumented command areas were:

- `universe *` commands for raw pools, candidates, rebalance, rotation, rollback, history, and symbol membership.
- `runtime replay`, `runtime replay-debug`, and `runtime replay-ingestion`.
- `research inspect-checkpoints`, `research plan-restart`, and `research resume-experiment`.
- `research list-components`, `research inspect-component`, `research summarize-generated-configs`.
- `backtesting verify-auto-promotion` and `backtesting verify-auto-demotion`.
- `features run-pipeline`.
- `operations verify-runtime-soak` and `diagnostics snapshot` as observability/debugging commands.

These are now represented in `docs/backend/cli/cli.md` and the relevant domain
README files.

## Docs Referencing Missing/Changed Commands

- `docs/backend/cli/runtime_harness_reference.md` does not reference missing parser commands directly in the inspected header, but its count and domain summary are stale. It now carries a historical status warning.
- `docs/backend/cli/strategy_generation.md` remains consistent with the current `research` strategy-generation commands found in the parser.
- No command names were removed from docs in this audit. Existing historical docs were preserved.

## Domain Coverage Gaps

Resolved in this audit:

- Added `docs/backend/universe/README.md`.
- Added CLI coverage READMEs for research, simulation, execution, ingestion, storage-lineage, runtime, safety, observability, and portfolio-governance.
- Updated `docs/README.md` to include the new universe domain.

Remaining gap:

- There is no dedicated `docs/backend/admin/` or `docs/backend/diagnostics/` folder. These commands are currently covered through operations/debugging and observability docs because they are operational inspection commands, not standalone backend domains.

## Universe CLI Findings

Search terms checked: universe, Universe, candidate universe, universe version,
rebalance, membership, `UniverseVersion`, and `UniverseMember`.

Universe CLI commands do exist and are substantial: 28 leaf commands are
registered in `src/autonomous_trading_platform/cli/commands/universe.py`.

Coverage added:

- `docs/backend/universe/README.md`
- `docs/backend/cli/cli.md` universe command list
- operations/debugging links for universe runtime, validation, ingestion-input,
  and rotation-status checks

Manual verification TODOs:

- Verify which universe mutating commands enforce approval, cadence, or operator
  policy outside argparse flags.
- Add examples only after behavior is tested against a seeded local database.

## Documentation Updates Made

- Updated `docs/backend/cli/cli.md`.
- Updated `docs/backend/cli/runtime_harness_reference.md`.
- Updated `docs/README.md`.
- Updated `docs/operations/runbooks/README.md`.
- Updated `docs/operations/debugging/README.md`.
- Created `docs/backend/universe/README.md`.
- Created `docs/backend/research/README.md`.
- Created `docs/backend/simulation/README.md`.
- Created `docs/backend/execution/README.md`.
- Created `docs/backend/ingestion/README.md`.
- Created `docs/backend/storage-lineage/README.md`.
- Created `docs/backend/runtime/README.md`.
- Created `docs/backend/safety/README.md`.
- Created `docs/backend/observability/README.md`.
- Created `docs/backend/portfolio-governance/README.md`.
- Created this audit file.

## Follow-Up TODOs

- Run command help in an installed environment to verify whether `atp` is
  supplied by tooling outside `pyproject.toml`.
- Review `backtesting run` implementation status and mark it clearly if it is a
  placeholder or legacy path.
- Reconcile `runtime_harness_reference.md` with the current 85-command inventory
  if it should remain more than a historical handbook.
- Add examples for mutating universe, safety, and execution commands after
  operator policy and local database prerequisites are verified.
- Consider adding generated parser inventory checks in CI or a docs maintenance
  script so command-count drift is caught earlier.
