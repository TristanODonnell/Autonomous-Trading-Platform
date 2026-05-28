# Safety CLI Domain Audit: `safety.py`

Target file: `src/autonomous_trading_platform/cli/commands/safety.py`

Scope note: `safety` should own platform protection: live-trading gate checks, kill switch, and emergency halt. Pause/resume and trading mode belong in `controls`; drawdown/exposure/limit policy belongs in `risk`; broad health/runbook validation belongs in `operations`.

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp safety` | `--help` | argparse only | no | no | yes | PASS_HELP_ONLY |
| `atp safety arm-live` | `--reason`, `--armed-by` | `handle_arm_live` | yes, process-local only | no | no | SUSPICIOUS |
| `atp safety disarm-live` | none | `handle_disarm_live` | yes, process-local only | no | no | SUSPICIOUS |
| `atp safety enable-kill-switch` | `--reason`, `--updated-by` | `handle_enable_kill_switch` | yes, intended DB mutation | no | no | LOCAL_DB_MUTATION |
| `atp safety disable-kill-switch` | `--reason`, `--updated-by` | `handle_disable_kill_switch` | yes, intended DB mutation | no | no | LOCAL_DB_MUTATION |
| `atp safety gate-status` | `--account-id` | `handle_gate_status` | conditional, may create singleton kill-switch row | no | no, not strictly read-only | SUSPICIOUS |

## 2. Domain Responsibility Check

| Command | Placement | Notes |
|---|---|---|
| `safety arm-live` | correctly placed, but current implementation should be fixed or deprecated | Live gate arming belongs in `safety`, but the service is in-memory per process, so this CLI command does not arm any later runtime/CLI process. |
| `safety disarm-live` | correctly placed, but current implementation should be fixed or deprecated | Live gate disarm belongs here, but this only disarms a fresh process-local gate. |
| `safety enable-kill-switch` | correctly placed | Kill switch belongs in `safety`; implementation should use the application emergency-halt path for commit/audit/open-order cancellation. |
| `safety disable-kill-switch` | correctly placed, with controls overlap | Releasing a kill switch is safety-owned, but operator resume semantics should be clearly separated or wrapped in `controls resume`. |
| `safety gate-status` | correctly placed | Live gate status belongs in `safety`; it should report details and use durable runtime-gate state if arming is meant to cross process boundaries. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Class | Implementation target service/function | Priority |
|---|---|---|---|---|---:|
| `atp safety status` | Show kill switch, runtime gate, environment gate, account allowlist result, and overall safety status. | A safety operator needs one safe status entrypoint before mutating protection state. | read-only | `LiveTradingGateService.get_gate_status`, `KillSwitchService.get_status`, `Settings`/`EnvironmentSafetyPolicy` | P0 |
| `atp safety kill-switch-status` | Read the persisted kill-switch state without requiring an account ID. | Kill switch is the primary safety latch and should be inspectable directly. | read-only | `KillSwitchService.get_status` | P0 |
| `atp safety emergency-halt --reason ... --triggered-by ...` | Activate kill switch, cancel/mark open orders, update runtime controls, write audit log, commit transaction. | This is the operationally correct safety action for emergency halt. | local-mutating, cross-domain execution ledger | `application.services.runtime_control_service.RuntimeControlService.activate_kill_switch` | P0 |
| `atp safety release-kill-switch --reason ... --updated-by ... --confirm-release` | Clear persisted kill switch with explicit confirmation. | Releasing protection is safety-sensitive and should be deliberate. | local-mutating | `RuntimeControlService.resume_trading` if intended to resume, or a committed `KillSwitchService.disable` path plus audit | P0 |
| `atp safety assert-gate --account-id <id>` | Return non-zero if live trading gate is not fully open. | Scripts and deployment checks need a fail-closed assertion command. | read-only | `LiveTradingGateService.assert_live_trading_allowed` | P1 |
| `atp safety arm-live --expires-at <iso>` | Support the existing `RuntimeGateService.arm(..., expires_at=...)` expiry capability. | Runtime arming should be time-bounded. | local-mutating if persisted | `RuntimeGateService.arm` plus durable store if added | P1 |
| `atp safety audit-log --limit 20` | Show recent safety actions such as kill switch loaded/activated/released and idempotency violations. | Safety state changes must be operator-auditable. | read-only | `AuditLogRepository.list_events` filtered by component/action | P1 |
| `atp safety startup-check --account-id <id>` | Emit startup safety state and validate kill switch/load gates before scheduler/API startup. | Startup fail-closed protection is safety domain behavior. | read-only or audit-mutating | `KillSwitchService.emit_startup_audit_event`, `LiveTradingGateService.get_gate_status` | P2 |
| `atp safety pre-trade-check --symbol AAPL --side buy --quantity 10 --price 185` | Validate order-level safety gates without submitting an order. | Pre-trade protection lives in safety, but limit policy details border `risk`. | read-only | `PreTradeRiskService`, `OrderThrottleService`, `OrderIdempotencyService` | P2 |

## 4. Testing Plan

Phase 0: help only

```powershell
atp safety --help
atp safety arm-live --help
atp safety disarm-live --help
atp safety enable-kill-switch --help
atp safety disable-kill-switch --help
atp safety gate-status --help
```

Phase 1: safe read-only commands

```powershell
atp safety gate-status --account-id paper-account-001
atp safety gate-status --account-id live-account-001
```

Recommended after extension:

```powershell
atp safety status --account-id live-account-001
atp safety kill-switch-status
atp safety assert-gate --account-id live-account-001
atp safety audit-log --limit 20
```

Phase 2: local DB mutation commands

Use only against a disposable local database until the CLI commit/audit behavior is corrected.

```powershell
atp safety enable-kill-switch --reason "operator emergency halt test" --updated-by "local-operator"
atp safety gate-status --account-id live-account-001
atp safety disable-kill-switch --reason "local emergency halt test complete" --updated-by "local-operator"
```

Recommended after extension:

```powershell
atp safety emergency-halt --reason "operator emergency halt test" --triggered-by "local-operator"
atp safety release-kill-switch --reason "halt test complete" --updated-by "local-operator" --confirm-release
```

Phase 3: cross-domain/runtime commands

```powershell
atp safety arm-live --reason "supervised live validation window" --armed-by "ops-lead"
atp safety gate-status --account-id live-account-001
atp safety disarm-live
```

Current caveat: these commands do not share runtime-gate state across CLI invocations, so this sequence will not prove durable arming. After persistence is added, repeat it and then run:

```powershell
atp runtime trigger-job --job-name trading_cycle
```

Phase 4: broker/external commands

No current safety CLI command directly calls a broker API. Emergency halt should remain DB-first unless intentionally extended to call broker cancellation endpoints; if broker cancellation is added, require a dry-run/preflight and explicit account/environment confirmation.

## 5. Risks / Suspicious Wiring

- `arm-live` and `disarm-live` use a new `RuntimeGateService()` every command invocation. The gate is in-memory only, so arming live trading exits with the CLI process and cannot affect a later `gate-status` or runtime process.
- `gate-status` also creates a fresh `RuntimeGateService()`, so `runtime_ok` will normally be false regardless of a prior `atp safety arm-live`.
- `RuntimeGateService.arm` supports `expires_at`, but the CLI has no `--expires-at`; live arming has no time-bound option.
- `build_dependencies()` opens a DB session but handlers never close it.
- `enable-kill-switch` and `disable-kill-switch` call `KillSwitchService` directly. That service flushes repository changes but does not commit; the CLI does not commit either, so persistence is questionable and may rollback on process/session close.
- The CLI kill-switch path bypasses `application.services.runtime_control_service.RuntimeControlService.activate_kill_switch`, which is the richer emergency-halt path that marks open orders cancelled, writes audit logs, updates both kill-switch/runtime-control state, and commits.
- `enable-kill-switch` does not clearly cancel/mark open orders. For an emergency halt, that is misleading compared with the REST `/controls/kill-switch` behavior.
- `disable-kill-switch` lowers a safety protection without a `--confirm-release` style guard.
- `gate-status` calls `KillSwitchStateRepository.get_current_state()`, which creates and flushes the singleton row if missing. A status command is therefore not strictly read-only on a fresh DB.
- No current safety CLI command emits a structured artifact file or audit evidence summary.
- No CLI tests for `safety.py` were found in `tests/cli`, while underlying safety service tests do exist.

## 6. Recommended Refactor / Extension

- Keep the domain; the command concepts belong in `safety`.
- Fix persistence and lifecycle before expanding: close sessions, commit intended mutations, and avoid process-local live-gate semantics unless explicitly documented as a same-process primitive.
- Route emergency halt through `RuntimeControlService.activate_kill_switch` or add equivalent commit/audit/open-order cancellation behavior to the CLI path.
- Rename `enable-kill-switch` to, or wrap it with, `emergency-halt` for the operator-facing action; keep low-level enable/disable only if needed for admin/debug use.
- Add `status`, `kill-switch-status`, `assert-gate`, `audit-log`, and a confirmed `release-kill-switch`.
- Add `--expires-at` to live arming and consider persisting runtime-gate state if CLI arming is intended to affect scheduler/API processes.
- Add JSON/artifact output options for safety actions and include audit IDs where available.
- Add explicit confirmation for protection-lowering commands.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp safety` | Help parent | yes | Low | Keep. |
| `atp safety arm-live` | Process-local only | yes | High | Persist or deprecate; add expiry. |
| `atp safety disarm-live` | Process-local only | yes | Medium | Persist or deprecate with `arm-live`. |
| `atp safety enable-kill-switch` | Intended protection mutation, weak wiring | yes | High | Route through emergency-halt service with commit/audit/order cancellation. |
| `atp safety disable-kill-switch` | Intended protection release, weak guard | yes | High | Add commit/audit and explicit confirmation. |
| `atp safety gate-status` | Useful but misleading runtime gate result | yes | Medium | Add durable gate state and direct status details. |
