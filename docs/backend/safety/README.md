# Safety CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `safety arm-live` | Arm live trading gate. | `src/autonomous_trading_platform/cli/commands/safety.py` | Safety-critical; requires reason and operator. |
| `safety disarm-live` | Disarm live trading gate. | `src/autonomous_trading_platform/cli/commands/safety.py` | Safety-critical. |
| `safety enable-kill-switch` | Enable kill switch. | `src/autonomous_trading_platform/cli/commands/safety.py` | Safety-critical; blocks trading. |
| `safety disable-kill-switch` | Disable kill switch. | `src/autonomous_trading_platform/cli/commands/safety.py` | Safety-critical; requires reason and operator. |
| `safety gate-status` | Inspect gate status for an account. | `src/autonomous_trading_platform/cli/commands/safety.py` | Requires `--account-id`. |

## Related Docs

- `docs/backend/safety/safety.md`
- `docs/backend/runtime/failure-modes.md`
