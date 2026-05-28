# Ingestion CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `ingestion run-bars` | Run market bar ingestion. | `src/autonomous_trading_platform/cli/commands/ingestion.py` | Mutating. |
| `ingestion run-backfill` | Run historical market backfill. | `src/autonomous_trading_platform/cli/commands/ingestion.py` | Requires symbols and date window; mutating. |
| `ingestion run-corporate-actions` | Run corporate action ingestion. | `src/autonomous_trading_platform/cli/commands/ingestion.py` | Mutating. |
| `ingestion inspect-bar` | Inspect one ingested bar. | `src/autonomous_trading_platform/cli/commands/ingestion.py` | Requires symbol and timestamp. |

## Related Docs

- `docs/backend/ingestion/ingestion.md`
- `docs/backend/orchestration/ingestion-cycle.md`
- `docs/backend/storage-lineage/storage.md`
