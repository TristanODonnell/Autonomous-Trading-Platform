# Execution CLI Domain Audit

Target CLI domain: `execution`
Target CLI file: `src/autonomous_trading_platform/cli/commands/execution.py`

## 1. Current CLI Inventory

| Command Path | Arguments / Options | Handler | Mutates State? | Calls External APIs? | Safe For Local Read-Only Testing? | Phase Classification |
|---|---|---|---:|---:|---:|---|
| `execution reconcile-order` | `--order-id` required | `handle_reconcile_order(args)` | yes | yes | no | `BROKER_OR_EXTERNAL` |
| `execution reconcile-open-orders` | none | `handle_reconcile_open_orders(_args, run_id)` | yes if callable | yes if callable | no | `SUSPICIOUS` |
| `execution inspect-order` | `--order-id` required | `handle_inspect_order(args)` | no | no | yes | `READ_ONLY_SAFE` |
| `execution inspect-position` | `--symbol` required | `handle_inspect_position(args)` | no | no | yes | `READ_ONLY_SAFE` |
| `execution inspect-cash` | none | `handle_inspect_cash(_args)` | no | no | yes | `READ_ONLY_SAFE` |

Notes:
- `reconcile-order` builds an execution context, which initializes the Alpaca broker client and broker startup health check when no injected client is supplied. It fetches broker order state, may upsert broker orders/fills, applies runtime state transitions, applies post-fill accounting, and writes risk snapshots.
- `reconcile-open-orders` is currently not callable from argparse as registered because its handler requires `run_id`, but the parser supplies only `args`.
- The inspect commands are local DB reads through `SorUnitOfWork`.

## 2. Domain Responsibility Check

| Command | Placement | Assessment |
|---|---|---|
| `execution reconcile-order` | correctly placed | Single-order broker reconciliation and fill/accounting updates are execution-domain responsibilities. Needs stronger guardrails. |
| `execution reconcile-open-orders` | correctly placed conceptually, but broken | Open-order reconciliation belongs in `execution`, but the parser/handler mismatch must be fixed before use. |
| `execution inspect-order` | correctly placed | Order, broker order, and fills inspection belongs in `execution`; can also be surfaced read-only in `diagnostics`. |
| `execution inspect-position` | should be duplicated/wrapped elsewhere | Position snapshot inspection is useful in `execution`, but portfolio-level position state should also be available in `portfolio`. |
| `execution inspect-cash` | should be duplicated/wrapped elsewhere | Cash snapshot inspection is useful after fills/reconciliation, but portfolio/cash state should also be exposed in `portfolio`. |

## 3. Missing CLI Coverage

| Proposed Command Path | Purpose | Why It Belongs In This Domain | Type | Implementation Target | Priority |
|---|---|---|---|---|---|
| `execution broker-health` | Verify broker credentials/account readiness without mutating trading state. | Broker readiness is the first execution preflight. | broker-facing read-only | `BrokerStartupHealthCheckService.assert_broker_ready(...)` | P0 |
| `execution sync-broker-state --run-id ...` | Sync broker account/cash, and optionally positions/open orders. | Recent broker runtime sync service is not exposed in CLI. | broker-facing local mutation | `BrokerRuntimeSyncService.sync_broker_runtime_state`, `sync_positions_from_broker`, `sync_open_orders_from_broker` | P0 |
| `execution validate-broker-consistency --tolerance 0.01` | Compare persisted account/cash snapshots against broker state. | Verifies persisted runtime state against broker truth. | broker-facing read-only | `BrokerRuntimeSyncService.validate_broker_runtime_consistency(...)` | P0 |
| `execution sync-position-snapshot --run-id ...` | Persist current broker positions into position snapshots. | Position sync is implemented and tested but CLI-missing. | broker-facing local mutation | `BrokerRuntimeSyncService.sync_positions_from_broker(...)` | P1 |
| `execution sync-open-orders --run-id ... --account-id paper-account-1` | Pull broker open orders and persist broker order rows. | Useful before/after reconciliation. | broker-facing local mutation | `BrokerRuntimeSyncService.sync_open_orders_from_broker(...)` | P1 |
| `execution sync-order-status --order-id broker-order-id --run-id ... --account-id ...` | Fetch one broker order status and persist latest broker order row. | Narrow status update for a known broker order. | broker-facing local mutation | `BrokerRuntimeSyncService.sync_order_status(...)` | P1 |
| `execution reconcile-order-fills --order-id broker-order-id --run-id ... --account-id ...` | Extract incremental fills from broker order state idempotently. | Recent fill reconciliation/intelligence path is service-backed but not CLI-backed. | broker-facing local mutation | `BrokerRuntimeSyncService.reconcile_order_fills(...)` | P1 |
| `execution external-reconcile --run-id ... --output artifacts/execution/reconciliation.json` | Generate broker-vs-platform reconciliation report for orders/fills/positions/cash/equity. | External reconciliation is a core execution operability feature. | broker-facing read-only plus artifact output | `ExternalBrokerReconciliationService.reconcile(...)`, reconciliation report persistence if available | P0 |
| `execution inspect-fill-quality --intent-id ...` | Inspect two-phase fill quality/slippage metrics for one intent/order/fill. | Execution intelligence updates produce fill-quality analytics but CLI cannot inspect them. | read-only | `FillQualityMetricsRepository.get_by_intent_id(...)` / query helpers | P0 |
| `execution list-fill-quality --run-id ... --adverse-only --limit 50` | List realised slippage/fill-quality records. | Makes adverse fill detection and policy analytics operable. | read-only | `FillQualityMetricsRepository`, `RealisedSlippageService` data model | P1 |
| `execution policy-preview --intent-id ... --policy-mode TWAP` | Show transformed order intent, order type, slices, expected slippage/cost without submitting. | Execution policy engine is important and currently opaque. | broker-facing read-only unless quotes are mocked; should support `--offline` | `ExecutionPolicyEngine.apply(...)`, `ExecutionPolicyConfig.from_dict(...)` | P1 |
| `execution inspect-runtime-state --strategy-id ...` | Show strategy/order runtime state used by submission/reconciliation. | Execution state machine debugging belongs here. | read-only | `StrategyRuntimeStateService`, `OrderRuntimeStateService`, repositories | P1 |
| `execution list-open-orders --source platform|broker|both` | List currently open tracked/broker orders. | Operators need a safe view before reconciling. | conditional: DB read or broker read | `TrackedOrderRepository.list_open_orders`, broker client `list_open_orders` | P1 |
| `execution cancel-order --broker-order-id ... --dry-run` | Cancel broker order with explicit guard. | Execution owns broker order lifecycle; cancellation needs strong safety gates. | broker-facing mutation | `OrderExecutionService.cancel_order(...)`, audit logging | P2 |
| `execution stream-orders --duration-seconds 60 --dry-run` | Start broker event stream briefly and report event processing health. | Broker event stream service exists but has no operator harness. | broker-facing/runtime | `BrokerEventStreamService`, `AlpacaOrderStreamClient`, `BrokerStreamFillProcessor` | P2 |
| `execution submit-intents --run-id ... --dry-run` | Submit pending/generated order intents through execution policy and safety guards. | Submission exists as scheduler job, but a guarded CLI harness would verify execution path. | broker-facing or dry-run | `run_order_submission_job(...)` or an execution command service | P2 |

## 4. Testing Plan

### Phase 0: Help Commands

```powershell
python -m autonomous_trading_platform.cli.main execution --help
python -m autonomous_trading_platform.cli.main execution reconcile-order --help
python -m autonomous_trading_platform.cli.main execution reconcile-open-orders --help
python -m autonomous_trading_platform.cli.main execution inspect-order --help
python -m autonomous_trading_platform.cli.main execution inspect-position --help
python -m autonomous_trading_platform.cli.main execution inspect-cash --help
```

### Phase 1: Safe Read-Only Commands

```powershell
python -m autonomous_trading_platform.cli.main execution inspect-order --order-id 00000000-0000-0000-0000-000000000101
python -m autonomous_trading_platform.cli.main execution inspect-position --symbol AAPL
python -m autonomous_trading_platform.cli.main execution inspect-cash
```

Recommended after new coverage:

```powershell
python -m autonomous_trading_platform.cli.main execution inspect-fill-quality --intent-id 00000000-0000-0000-0000-000000000201
python -m autonomous_trading_platform.cli.main execution list-fill-quality --run-id 00000000-0000-0000-0000-000000000301 --adverse-only --limit 25
python -m autonomous_trading_platform.cli.main execution inspect-runtime-state --strategy-id baseline_strategy
```

### Phase 2: Local DB Mutation Commands

No current command is purely local-mutating; mutation commands initialize broker-backed execution dependencies.

Future local-only/offline examples:

```powershell
python -m autonomous_trading_platform.cli.main execution policy-preview --intent-id 00000000-0000-0000-0000-000000000201 --policy-mode TWAP --offline --mid-price 185.25
```

### Phase 3: Cross-Domain / Runtime Commands

`reconcile-open-orders` is intended to be a runtime-like job command but is currently broken. After fixing parser wiring:

```powershell
python -m autonomous_trading_platform.cli.main execution reconcile-open-orders --run-id 00000000-0000-0000-0000-000000000301
```

### Phase 4: Broker / External Commands

Use paper credentials only unless a separate live safety gate explicitly permits live operation:

```powershell
python -m autonomous_trading_platform.cli.main execution reconcile-order --order-id 00000000-0000-0000-0000-000000000101
python -m autonomous_trading_platform.cli.main execution broker-health
python -m autonomous_trading_platform.cli.main execution sync-broker-state --run-id 00000000-0000-0000-0000-000000000301
python -m autonomous_trading_platform.cli.main execution validate-broker-consistency --tolerance 0.01
python -m autonomous_trading_platform.cli.main execution external-reconcile --run-id 00000000-0000-0000-0000-000000000301 --output artifacts/execution/reconciliation_000000000301.json
```

## 5. Risks / Suspicious Wiring

- `execution reconcile-open-orders` has a handler signature mismatch: parser sets `func=handle_reconcile_open_orders`, but the handler signature is `handle_reconcile_open_orders(_args, run_id)`. `run_handler` will pass only `args`, so this command should fail at invocation.
- `execution reconcile-open-orders` parser has no `--run-id`, even though the scheduler job requires `run_id`.
- `build_cli_execution_dependencies(...)` initializes `AlpacaBrokerClient` and runs broker startup health checks for reconciliation paths. This means even a single-order reconcile is broker-facing immediately.
- `execution reconcile-order` has no `--dry-run`, but it can upsert broker orders, fills, position/cash snapshots, order runtime state, and risk snapshots.
- `execution reconcile-order` has no explicit `--paper-only`, environment confirmation, live gate, or kill-switch visibility at the CLI layer.
- `execution reconcile-order` returns useful JSON, but does not emit an artifact file or include an audit-log event ID.
- Current inspect commands are useful but narrow. They do not expose broker account snapshots, broker runtime sync state, reconciliation reports, order runtime states, strategy runtime states, or fill quality metrics.
- Execution intelligence services added in the codebase are not represented in the CLI: broker runtime sync, broker consistency validation, external broker reconciliation reports, stream fill processing, execution policy preview, realised slippage/fill-quality inspection, and broker health checks.
- There are focused service tests under `tests/execution`, but no focused CLI tests for `execution.py` were found.
- Some execution features overlap with `risk` and `portfolio`; keep capital/risk-limit interpretation in `risk`, but expose execution-produced snapshots and order/fill lifecycle under `execution`.

## 6. Recommended Refactor / Extension

- Keep the `execution` domain.
- Fix `reconcile-open-orders` by adding `--run-id` and changing the handler to accept only `args`.
- Add `--dry-run`, `--paper-only`, `--output`, and structured JSON options to mutating/broker-facing commands.
- Add CLI safety gates before broker mutation/cancel/submission commands.
- Add read-only inspection for fill quality, runtime state, broker account snapshots, and open orders.
- Add broker runtime sync and external reconciliation commands; these appear to be the largest gap from the recent execution-intelligence work.
- Add execution policy preview so TWAP/VWAP/limit/slippage behavior can be validated before live submission.
- Add audit logging for CLI-initiated mutating execution actions and include audit IDs in output.
- Add CLI tests for parser wiring, especially `reconcile-open-orders`, and mocked broker tests for broker-facing command handlers.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `execution reconcile-order` | Functional broker-facing mutation path | yes | High | Add dry-run, safety gate, output artifact/audit ID, mocked CLI tests |
| `execution reconcile-open-orders` | Registered but handler/parser mismatch makes it broken | yes | High | Add `--run-id`, fix handler signature, test invocation |
| `execution inspect-order` | Useful read-only DB inspection | yes | Low | Keep; add filters/output format and runtime-state linkage |
| `execution inspect-position` | Useful read-only latest position snapshot | partial | Low | Keep; duplicate richer view under `portfolio` |
| `execution inspect-cash` | Useful read-only latest cash snapshot | partial | Low | Keep; duplicate richer view under `portfolio` |
