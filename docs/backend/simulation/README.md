# Simulation CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `backtesting run` | Run one backtest entry point. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | TODO: manually verify whether this path remains implemented or placeholder. |
| `backtesting inspect-results` | Inspect backtest results by run id. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Requires `--run-id`. |
| `backtesting seed-fixture` | Seed a backtesting fixture. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Mutating unless `--dry-run`. |
| `backtesting seed-settings` | Seed operator/settings fixture. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Mutating. |
| `backtesting read-settings` | Read seeded settings. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Read-only. |
| `backtesting seed-controls` | Seed control state fixture. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Mutating; supports `--clean`. |
| `backtesting read-controls` | Read control state. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Read-only. |
| `backtesting read-portfolio` | Read portfolio state. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Read-only. |
| `backtesting read-dashboard` | Read dashboard-facing state. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Read-only. |
| `backtesting verify-risk-parameter-effects` | Verify risk settings against replay/simulation. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Requires controls, settings, symbols, and date window. |
| `backtesting verify-notification-events` | Verify notification events. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Requires controls and settings. |
| `backtesting verify-governance-allocation` | Verify governance/allocation behavior. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Portfolio-governance coverage. |
| `backtesting verify-auto-promotion` | Verify auto-promotion. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Requires settings. |
| `backtesting verify-auto-demotion` | Verify auto-demotion. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Requires settings. |

## Related Docs

- `docs/backend/simulation/research_execution_paths.md`
- `docs/backend/execution/execution_policy_simulation_parity.md`
- `docs/backend/portfolio-governance/README.md`
