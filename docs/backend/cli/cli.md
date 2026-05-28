# CLI Interface

Status: Current as of CLI documentation drift audit.

The CLI is implemented with `argparse` in `src/autonomous_trading_platform/cli/`.
The parser program name is `atp`, and `src/autonomous_trading_platform/cli/main.py`
is the source of truth for command-group registration.

## Entrypoint

Code entrypoint:

```bash
python -m autonomous_trading_platform.cli.main <domain> <command> [options]
```

Parser display name:

```bash
atp <domain> <command> [options]
```

Packaging note: `pyproject.toml` does not currently define a `[project.scripts]`
console-script alias for `atp`. If an installed `atp` executable exists in an
environment, it is provided outside the package metadata inspected in this audit.

## Command Discovery

Use the parser help as the live command reference:

```bash
python -m autonomous_trading_platform.cli.main --help
python -m autonomous_trading_platform.cli.main <domain> --help
python -m autonomous_trading_platform.cli.main <domain> <command> --help
```

The main parser registers these top-level domains:

- `admin`
- `backtesting`
- `diagnostics`
- `execution`
- `features`
- `ingestion`
- `operations`
- `research`
- `runtime`
- `safety`
- `strategy`
- `universe`

## Command Groups

| Group | Source | Commands | Backend domain | Domain docs |
|---|---|---:|---|---|
| `admin` | `src/autonomous_trading_platform/cli/commands/admin.py` | 3 | Operations / runtime inspection | `docs/operations/debugging/README.md` |
| `backtesting` | `src/autonomous_trading_platform/cli/commands/backtesting.py` | 14 | Simulation, verification, portfolio/governance checks | `docs/backend/simulation/README.md`, `docs/backend/portfolio-governance/README.md` |
| `diagnostics` | `src/autonomous_trading_platform/cli/commands/diagnostics.py` | 1 | Runtime diagnostics | `docs/operations/debugging/README.md` |
| `execution` | `src/autonomous_trading_platform/cli/commands/execution.py` | 5 | Execution, broker reconciliation, cash/position inspection | `docs/backend/execution/README.md` |
| `features` | `src/autonomous_trading_platform/cli/commands/features.py` | 1 | Feature datasets and storage lineage | `docs/backend/storage-lineage/README.md` |
| `ingestion` | `src/autonomous_trading_platform/cli/commands/ingestion.py` | 4 | Market data and corporate action ingestion | `docs/backend/ingestion/README.md` |
| `operations` | `src/autonomous_trading_platform/cli/commands/operations.py` | 1 | Runtime soak verification | `docs/backend/observability/README.md`, `docs/operations/runbooks/README.md` |
| `research` | `src/autonomous_trading_platform/cli/commands/research.py` | 11 | Research, strategy generation, simulations, checkpoints | `docs/backend/research/README.md` |
| `runtime` | `src/autonomous_trading_platform/cli/commands/runtime.py`, `runtime_soak_loop.py` | 10 | Runtime cycles, job triggering, replay, soak loops | `docs/backend/runtime/README.md` |
| `safety` | `src/autonomous_trading_platform/cli/commands/safety.py` | 5 | Runtime safety and kill switch controls | `docs/backend/safety/README.md` |
| `strategy` | `src/autonomous_trading_platform/cli/commands/strategy.py` | 2 | Strategy evaluation/readiness | `docs/backend/research/README.md` |
| `universe` | `src/autonomous_trading_platform/cli/commands/universe.py` | 28 | Universe selection, raw pool, candidates, rotation, rebalance, history | `docs/backend/universe/README.md` |

## Command Inventory

### Safety

- `safety arm-live`
- `safety disarm-live`
- `safety enable-kill-switch`
- `safety disable-kill-switch`
- `safety gate-status`

Safety commands can modify live-trading enablement or kill-switch state. Review
`docs/backend/safety/README.md` before running mutating commands.

### Runtime, Operations, Admin, Diagnostics

- `runtime run-cycle`
- `runtime trigger-job`
- `runtime inspect-manifest`
- `runtime inspect-audit`
- `runtime soak-loop backtest`
- `runtime soak-loop paper`
- `runtime soak-loop research`
- `runtime replay`
- `runtime replay-debug`
- `runtime replay-ingestion`
- `operations verify-runtime-soak`
- `admin inspect-config`
- `admin inspect-env`
- `admin inspect-failed-runs`
- `diagnostics snapshot`

Runtime commands may create run manifests, runtime job rows, replay artifacts, or
broker-facing paper-trading activity depending on the command and options.

### Ingestion, Features, Universe

- `ingestion run-bars`
- `ingestion run-backfill`
- `ingestion run-corporate-actions`
- `ingestion inspect-bar`
- `features run-pipeline`
- `universe select-now`
- `universe inspect-active`
- `universe inspect-symbols`
- `universe inspect-symbol`
- `universe validate-active`
- `universe validation-report`
- `universe inspect-ingestion-input`
- `universe seed`
- `universe raw-pool-refresh`
- `universe raw-pool-inspect`
- `universe raw-pool-inspect-symbol`
- `universe candidate-generate`
- `universe candidate-inspect`
- `universe candidate-inspect-rejections`
- `universe candidate-inspect-symbol`
- `universe history`
- `universe runtime-status`
- `universe observability-status`
- `universe propose-rebalance`
- `universe rotate`
- `universe rollback`
- `universe rotation-history`
- `universe rebalance-history`
- `universe rotation-status`
- `universe history-for-date`
- `universe replay-timeline`
- `universe compare-universes`
- `universe symbol-history`

Ingestion, feature, and mutating universe commands can write data versions,
universe versions, raw-pool rows, candidate versions, rebalance proposals, or
rotation records.

### Strategy, Research, Backtesting

- `strategy evaluate-bar`
- `strategy inspect-readiness`
- `research run-simulation`
- `research run-experiment`
- `research list-strategy-types`
- `research inspect-strategy`
- `research list-components`
- `research inspect-component`
- `research generate-strategies`
- `research summarize-generated-configs`
- `research inspect-checkpoints`
- `research plan-restart`
- `research resume-experiment`
- `backtesting run`
- `backtesting inspect-results`
- `backtesting seed-fixture`
- `backtesting seed-settings`
- `backtesting read-settings`
- `backtesting seed-controls`
- `backtesting read-controls`
- `backtesting read-portfolio`
- `backtesting read-dashboard`
- `backtesting verify-risk-parameter-effects`
- `backtesting verify-notification-events`
- `backtesting verify-governance-allocation`
- `backtesting verify-auto-promotion`
- `backtesting verify-auto-demotion`

Research and backtesting commands are not live execution commands, but several
write fixtures, settings, controls, simulation state, or checkpoint outputs.

### Execution and Broker

- `execution reconcile-order`
- `execution reconcile-open-orders`
- `execution inspect-order`
- `execution inspect-position`
- `execution inspect-cash`

Reconciliation commands can update local broker/order state. Inspection commands
are intended for read-only operational debugging.

## Deprecated / Legacy Notes

No parser-registered command is explicitly marked deprecated in the current CLI
source. The longer operator handbook at `runtime_harness_reference.md` contains
historical "legacy/deprecated/cleanup" notes and is now secondary to this index
for current command inventory.

## Related CLI Docs

- `runtime_harness_reference.md` - detailed operator handbook; command counts may
  lag the current parser.
- `strategy_generation.md` - focused reference for `research generate-strategies`
  and related registry/component commands.
- `docs/audits/agent-findings/cli_documentation_drift_audit.md` - drift audit and
  command inventory from the current parser.
