# Runbooks

Status: DRAFT

Use `docs/backend/cli/cli.md` as the central CLI index.

## CLI Entry Points

- Runtime verification: `operations verify-runtime-soak`, `diagnostics snapshot`
- Paper trading checks: `runtime soak-loop paper`
- Broker reconciliation: `execution reconcile-order`, `execution reconcile-open-orders`
- Ingestion/debugging: `ingestion run-bars`, `ingestion run-backfill`, `ingestion inspect-bar`
- Research/simulation replay: `runtime replay`, `runtime replay-debug`, `runtime soak-loop research`
- Universe operations: `universe runtime-status`, `universe validation-report`, `universe rotation-status`

## Related Docs

- `docs/backend/cli/cli.md`
- `docs/backend/runtime/README.md`
- `docs/backend/execution/README.md`
- `docs/backend/ingestion/README.md`
- `docs/backend/observability/README.md`
