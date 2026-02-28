# RunManifest

## Purpose
- Canonical record describing the full configuration and environment of a single run.
- Guarantees reproducibility of strategy behavior, data inputs, and execution settings.
- Serves as the root audit document for all artifacts generated under `run_id`.

## Producer / Consumer
- Produced by:
  - Orchestrator / Run Bootstrap
- Consumed by:
  - Strategy Engine
  - Data Pipelines
  - Risk Engine
  - Execution Layer
  - Ledger
  - Audit / Reporting

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `run_id` | uuid | yes | Primary identifier for the run. |
| `run_type` | enum | yes | `backtest`, `paper`, `live`, `shadow`. |
| `created_at` | datetime (UTC) | yes | Run start time. |
| `environment` | string | yes | e.g. `paper` or `live` plus account context. |
| `broker` | string | yes | `"alpaca"` in v1. |
| `broker_account_id` | string | yes | Explicit allowlisted account. |
| `strategy_id` | string | yes | Strategy identifier. |
| `strategy_version` | string | yes | Version tag for strategy logic/config. |
| `strategy_config` | json | yes | Full resolved config used for this run. |
| `capital_bucket` | float | yes | Capital allocated to this run/strategy. |
| `interval` | enum | yes | `"5m"` for v1. |
| `start_date` | date | yes | For backtests; for paper/live can be “today” reference. |
| `end_date` | date | no | For backtests. |
| `dataset_version` | string | yes | Version for MarketBar + CorporateAction datasets. |
| `universe_version` | string | yes | Version for UniverseSnapshot. |
| `cost_model` | json | no | Cost model name + params (backtest). |
| `fill_model` | json | no | Fill model name + params (backtest). |
| `random_seed` | int | no | Seed for stochastic components. |
| `git_commit` | string | yes | Source revision. |
| `docker_image` | string | no | Container pin (or build hash). |
| `python_version` | string | no | Runtime metadata. |
| `dependency_lock_hash` | string | no | Hash of lockfile. |
| `notes` | string | no | Free-form annotations. |

## Invariants (Must Always Be True)
- RunManifest is **immutable once run starts** (append-only corrections via new manifest version, never overwrite).
- `run_id` is globally unique.
- `dataset_version` + `universe_version` are present for any run that evaluates strategies (audit requirement).
- `broker_account_id` must be explicitly allowlisted (capital-protection).
- If `run_type="live"`, `broker_account_id` must match an explicit allowlist and `environment` must be `"live"`.
- If `run_type="backtest"`, both `start_date` and `end_date` must be defined.
- `strategy_config` must be fully resolved (no environment-dependent placeholders).
- `capital_bucket > 0`.
- If `run_type="backtest"`, `random_seed` must be defined.
- If `run_type="backtest"`, `cost_model` and `fill_model` must be defined and version-pinned.
- 


## Validation Rules (Planning-Level)
- Check: missing git_commit or dataset_version/universe_version => halt run at bootstrap.
- Check: run_type=live but NO_LIVE_TRADING gate enabled => refuse to start.
- Check: if `run_type="live"` and `git_commit` is dirty/uncommitted => refuse to start.
- Check: missing `strategy_version` => halt.
- Check: missing `capital_bucket` => halt.


## Versioning
- `schema_version`: 1
- Compatibility rules:
  - Additive fields allowed (consumers ignore unknown fields).
  - Breaking changes require `schema_version += 1`.
- RunManifest records are immutable once persisted.
- Any correction requires emitting a new RunManifest version with a new `run_id`.