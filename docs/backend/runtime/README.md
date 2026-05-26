# Runtime CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `runtime run-cycle` | Run trading cycle. | `src/autonomous_trading_platform/cli/commands/runtime.py` | Mutating runtime path. |
| `runtime trigger-job` | Manually trigger scheduler job. | `src/autonomous_trading_platform/cli/commands/runtime.py` | Requires `--job-name`; subject to job registry behavior. |
| `runtime inspect-manifest` | Inspect run manifest. | `src/autonomous_trading_platform/cli/commands/runtime.py` | Requires `--run-id`. |
| `runtime inspect-audit` | Inspect audit events for run. | `src/autonomous_trading_platform/cli/commands/runtime.py` | Requires `--run-id`. |
| `runtime soak-loop backtest` | Run backtest soak loop. | `src/autonomous_trading_platform/cli/commands/runtime_soak_loop.py` | Requires symbols and date window. |
| `runtime soak-loop paper` | Run paper-trading soak loop. | `src/autonomous_trading_platform/cli/commands/runtime_soak_loop.py` | Broker-facing paper trading path. |
| `runtime soak-loop research` | Run research soak loop. | `src/autonomous_trading_platform/cli/commands/runtime_soak_loop.py` | Requires symbols and date window. |
| `runtime replay` | Run runtime replay. | `src/autonomous_trading_platform/cli/commands/runtime.py` | Mutating local replay state. |
| `runtime replay-debug` | Run debug replay. | `src/autonomous_trading_platform/cli/commands/runtime.py` | Debug-focused replay path. |
| `runtime replay-ingestion` | Replay ingestion over a window. | `src/autonomous_trading_platform/cli/commands/runtime.py` | Can run trading when `--run-trading` is passed. |

## Related Docs

- `docs/backend/orchestration/trading-cycle.md`
- `docs/backend/runtime/failure-modes.md`
- `docs/backend/cli/runtime_harness_reference.md`
