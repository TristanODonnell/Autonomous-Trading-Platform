# Portfolio Governance CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `backtesting verify-governance-allocation` | Verify governance/allocation behavior. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Uses controls/settings and total capital. |
| `backtesting verify-auto-promotion` | Verify auto-promotion behavior. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Requires settings. |
| `backtesting verify-auto-demotion` | Verify auto-demotion behavior. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Requires settings. |
| `backtesting read-portfolio` | Read portfolio state. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Dashboard/portfolio inspection. |
| `backtesting read-dashboard` | Read dashboard state. | `src/autonomous_trading_platform/cli/commands/backtesting.py` | Dashboard inspection. |

## Related Docs

- `docs/audits/agent-findings/portfolio_governance_allocation_audit.md`
- `docs/backend/safety/safety.md`
- `docs/backend/simulation/README.md`
