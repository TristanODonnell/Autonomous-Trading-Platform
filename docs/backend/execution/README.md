# Execution CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `execution reconcile-order` | Reconcile one broker order. | `src/autonomous_trading_platform/cli/commands/execution.py` | Mutating; requires `--order-id`. |
| `execution reconcile-open-orders` | Reconcile all open orders. | `src/autonomous_trading_platform/cli/commands/execution.py` | Mutating broker/order state sync. |
| `execution inspect-order` | Inspect one order. | `src/autonomous_trading_platform/cli/commands/execution.py` | Requires `--order-id`. |
| `execution inspect-position` | Inspect one position. | `src/autonomous_trading_platform/cli/commands/execution.py` | Requires `--symbol`. |
| `execution inspect-cash` | Inspect cash state. | `src/autonomous_trading_platform/cli/commands/execution.py` | Read-oriented. |

## Related Docs

- `docs/backend/execution/execution.md`
- `docs/backend/execution/execution_policy_simulation_parity.md`
- `docs/backend/broker/broker_event_stream_and_order_lifecycle.md`
