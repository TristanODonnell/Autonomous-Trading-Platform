# Storage Lineage CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `features run-pipeline` | Build feature datasets for a dataset version and optional symbol/date window. | `src/autonomous_trading_platform/cli/commands/features.py` | Mutating; writes feature dataset outputs. |

## Related Docs

- `docs/backend/storage-lineage/storage.md`
- `docs/backend/storage-lineage/universe.md`
- `docs/backend/storage-lineage/feature_dependency_resolution.md`
- `docs/backend/storage-lineage/indicator_vs_feature_architecture.md`
