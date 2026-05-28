# Observability CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `operations verify-runtime-soak` | Verify runtime soak window health. | `src/autonomous_trading_platform/cli/commands/operations.py` | Requires window start/end. |
| `diagnostics snapshot` | Emit runtime diagnostic snapshot. | `src/autonomous_trading_platform/cli/commands/diagnostics.py` | Supports `--json`. |
| `universe observability-status` | Inspect universe observability status. | `src/autonomous_trading_platform/cli/commands/universe.py` | Universe-specific observability. |

## Related Docs

- `docs/backend/observability/instrumentation_inventory.md`
- `docs/backend/observability/correlation_conventions.md`
- `docs/backend/observability/alerting.md`
- `docs/operations/runbooks/README.md`
