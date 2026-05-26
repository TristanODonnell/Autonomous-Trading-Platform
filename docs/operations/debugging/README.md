# Debugging

Status: DRAFT

Use `docs/backend/cli/cli.md` as the central CLI index.

## CLI Debugging Map

- Runtime cycle failures: `runtime inspect-manifest`, `runtime inspect-audit`, `admin inspect-failed-runs`
- Ingestion readiness misses: `ingestion inspect-bar`, `universe inspect-ingestion-input`, `strategy inspect-readiness`
- Reconciliation drift: `execution inspect-order`, `execution inspect-position`, `execution inspect-cash`
- Local runtime verification: `diagnostics snapshot`, `operations verify-runtime-soak`
- Simulation/replay debugging: `runtime replay-debug`, `backtesting inspect-results`

## Related Docs

- `docs/backend/cli/cli.md`
- `docs/backend/runtime/failure-modes.md`
- `docs/backend/execution/execution.md`
- `docs/backend/observability/instrumentation_inventory.md`
