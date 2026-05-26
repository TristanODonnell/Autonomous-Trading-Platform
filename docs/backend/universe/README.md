# Universe CLI

Status: Current as of CLI drift audit.

Universe commands manage and inspect universe selection, raw market pools,
candidate universes, active universe versions, rebalance proposals, rotations,
rollbacks, and historical membership.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `universe select-now` | Run universe selection for a timestamp. | `src/autonomous_trading_platform/cli/commands/universe.py` | May write/select active universe state. |
| `universe inspect-active` | Inspect the active universe. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read-oriented. |
| `universe inspect-symbols` | List active universe symbols. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read-oriented. |
| `universe inspect-symbol` | Inspect one symbol in the active universe. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--symbol`. |
| `universe validate-active` | Validate active universe invariants. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read/validation. |
| `universe validation-report` | Produce active universe validation report. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read/validation. |
| `universe inspect-ingestion-input` | Inspect symbols supplied to ingestion. | `src/autonomous_trading_platform/cli/commands/universe.py` | Connects universe membership to ingestion. |
| `universe seed` | Seed a custom universe from symbols. | `src/autonomous_trading_platform/cli/commands/universe.py` | Mutating; requires `--symbols`. |
| `universe raw-pool-refresh` | Refresh raw market pool. | `src/autonomous_trading_platform/cli/commands/universe.py` | Mutating; supports `--force`. |
| `universe raw-pool-inspect` | Inspect raw market pool rows. | `src/autonomous_trading_platform/cli/commands/universe.py` | Optional asset/exchange filters. |
| `universe raw-pool-inspect-symbol` | Inspect raw-pool data for one symbol. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--symbol`. |
| `universe candidate-generate` | Generate a candidate universe version. | `src/autonomous_trading_platform/cli/commands/universe.py` | Mutating; uses price/ADDV/size filters. |
| `universe candidate-inspect` | Inspect a candidate version. | `src/autonomous_trading_platform/cli/commands/universe.py` | Optional `--version-id`. |
| `universe candidate-inspect-rejections` | Inspect candidate rejection reasons. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--version-id`. |
| `universe candidate-inspect-symbol` | Inspect one candidate symbol. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--symbol`. |
| `universe history` | List universe versions. | `src/autonomous_trading_platform/cli/commands/universe.py` | Optional status filter. |
| `universe runtime-status` | Inspect runtime universe status. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read-oriented. |
| `universe observability-status` | Inspect universe observability status. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read-oriented. |
| `universe propose-rebalance` | Create or dry-run a rebalance proposal. | `src/autonomous_trading_platform/cli/commands/universe.py` | Mutating unless `--dry-run`. |
| `universe rotate` | Rotate/promote universe membership. | `src/autonomous_trading_platform/cli/commands/universe.py` | Mutating unless `--dry-run`; supports approval metadata. |
| `universe rollback` | Roll back to a target universe version. | `src/autonomous_trading_platform/cli/commands/universe.py` | Mutating unless `--dry-run`; requires target and reason. |
| `universe rotation-history` | List rotation records. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read-oriented. |
| `universe rebalance-history` | List rebalance history. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read-oriented. |
| `universe rotation-status` | Inspect current rotation status. | `src/autonomous_trading_platform/cli/commands/universe.py` | Read-oriented. |
| `universe history-for-date` | Resolve universe history at a timestamp. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--timestamp`. |
| `universe replay-timeline` | Replay universe timeline over a window. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--start` and `--end`. |
| `universe compare-universes` | Compare two universe versions. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--version-a` and `--version-b`. |
| `universe symbol-history` | Inspect one symbol's membership history. | `src/autonomous_trading_platform/cli/commands/universe.py` | Requires `--symbol`. |

## Related Docs

- `docs/backend/storage-lineage/universe.md`
- `docs/backend/storage-lineage/storage.md`
- `docs/backend/orchestration/scheduler.md`
- `docs/backend/cli/cli.md`

## TODOs

- Manually verify which mutating universe commands require approval or operator
  policy outside argparse flags.
- Add examples after command behavior is verified against a seeded local database.
