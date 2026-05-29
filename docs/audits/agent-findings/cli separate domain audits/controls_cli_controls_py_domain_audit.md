# Controls CLI Domain Audit: `controls.py`

Target CLI domain: `controls`

Target CLI file: `src/autonomous_trading_platform/cli/commands/controls.py`

Audit status: target file does not exist yet. This audit records the empty current inventory and the proposed controls-domain entrypoints needed for pause/resume, trading mode, strategy toggles, and allocation overrides.

Domain definition: `controls` owns operator controls that change platform behavior without changing long-lived configuration: pause/resume, trading mode, strategy enable/disable, and manual allocation overrides. It should not own kill switch/live gate/emergency halt (`safety`), risk policy definition (`risk`), operator settings (`settings`), scheduler orchestration (`runtime`), REST/frontend smoke checks (`api`), or full product workflows (`platform`).

## 1. Current CLI Inventory

No commands are registered in `src/autonomous_trading_platform/cli/commands/controls.py` because the file does not exist. The `controls` domain is also not registered in `src/autonomous_trading_platform/cli/main.py`.

| Command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp controls` | none | none | no | no | no | PLACEHOLDER |

Control-like commands currently live elsewhere:

| Existing command path | Arguments/options | Handler function | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `atp backtesting seed-controls` | `--config`, `--clean` | `handle_seed_controls` | yes | no | no | LOCAL_DB_MUTATION |
| `atp backtesting read-controls` | none | `handle_read_controls` | conditional | no | mostly | READ_ONLY_SAFE |
| `atp safety enable-kill-switch` | `--reason`, `--updated-by` | `handle_enable_kill_switch` | yes | no | no | LOCAL_DB_MUTATION |
| `atp safety disable-kill-switch` | `--reason`, `--updated-by` | `handle_disable_kill_switch` | yes | no | no | LOCAL_DB_MUTATION |
| `atp safety gate-status` | `--account-id` | `handle_gate_status` | no | conditional | conditional | BROKER_OR_EXTERNAL |

Note: `backtesting read-controls` is actually read-only. `handle_read_controls` calls `RuntimeControlStateRepository.get_global_state()` (returns `None` if no row exists), not `get_or_create_global_state()`. The "conditional mutation" concern applies to `RuntimeControlService.get_controls_state()` which uses the create-on-read path, not to this specific handler.

## 2. Domain Responsibility Check

| Command | Classification | Correct domain | Notes |
|---|---|---|---|
| `atp controls` | correctly placed, missing | controls | The domain belongs in the final CLI taxonomy but has not been implemented. |
| `atp backtesting seed-controls` | should move to another domain | controls, with fixture seeding split to platform/testing | Runtime controls, strategy toggles, and allocation overrides are controls. Creating strategy governance fixtures is broader than controls and should stay in fixture/platform tooling. |
| `atp backtesting read-controls` | should move to another domain | controls | Read-only control state belongs here. |
| `atp safety enable-kill-switch` | correctly placed | safety | Kill switch is platform protection, not routine controls. Controls may display kill switch state but should not own activation. |
| `atp safety disable-kill-switch` | correctly placed | safety | Clearing kill switch is safety-domain behavior. |
| `atp safety gate-status` | correctly placed | safety | Live gate status and account gating belong to safety. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Class | Implementation target service/function | Priority |
|---|---|---|---|---|---:|
| `atp controls state` | Show global controls and active strategy control states. | Core read path for operator controls. | read-only, but may create default row unless repository behavior changes | `RuntimeControlService.get_controls_state` | P0 |
| `atp controls state --json` | Emit machine-readable controls state. | Needed for audits, scripts, and CI checks. | read-only, but may create default row unless repository behavior changes | `RuntimeControlService.get_controls_state` | P0 |
| `atp controls pause --updated-by local-operator --reason "market data incident"` | Soft-pause trading without engaging kill switch. | Pause/resume is explicitly controls-domain behavior. | local-mutating | `RuntimeControlService.pause_trading` | P0 |
| `atp controls resume --updated-by local-operator --reason "market data recovered"` | Resume from soft pause. | Routine operator resume belongs to controls; kill-switch release should remain safety. | local-mutating, with safety caveat | `RuntimeControlService.resume_trading` or a split soft-resume method | P0 |
| `atp controls mode set --mode simulation --updated-by local-operator --reason "research run"` | Change trading mode among `simulation`, `paper`, and `live`. | Trading mode is a runtime operator control. | local-mutating | `RuntimeControlService.update_trading_mode` | P0 |
| `atp controls mode show` | Show current trading mode and last rationale. | Operators need low-friction mode inspection. | read-only | `RuntimeControlService.get_controls_state` | P0 |
| `atp controls strategy list` | List active paper/live strategies with enabled state and reasons. | Strategy toggles are part of controls. | read-only | `RuntimeControlService.get_controls_state` or `StrategyCatalogService` | P1 |
| `atp controls strategy disable --strategy-id momentum_v1 --updated-by local-operator --reason "operator pause"` | Disable one strategy. | Strategy-level operator toggle. | local-mutating | `StrategyControlService.set_enabled(enabled=False)` | P0 |
| `atp controls strategy enable --strategy-id momentum_v1 --updated-by local-operator --reason "operator resume"` | Re-enable one approved strategy. | Strategy-level operator toggle. | local-mutating | `StrategyControlService.set_enabled(enabled=True)` | P0 |
| `atp controls allocation list` | List current active allocation overrides and active allocation percentages. | Allocation overrides are explicitly controls-domain behavior. | read-only | `StrategyAllocationService.get_allocations_for_active_strategies` | P1 |
| `atp controls allocation status` | Show aggregate allocation usage and remaining override capacity. | Operators need to know whether a proposed override fits the allocation budget. | read-only | `StrategyAllocationService.get_aggregate_allocation_status` | P1 |
| `atp controls allocation preview --strategy-id momentum_v1 --allocation-pct 25` | Validate a proposed allocation override without writing. | Manual allocation changes need dry-run/preview. | read-only | Existing aggregate projection helpers in `StrategyAllocationService`, or a new public preview method | P0 |
| `atp controls allocation set --strategy-id momentum_v1 --allocation-pct 25 --updated-by risk-manager --reason "risk rebalance"` | Create or replace active allocation override. | Manual allocation override is a controls entrypoint. | local-mutating | `StrategyAllocationService.override_allocation` | P0 |
| `atp controls allocation clear --strategy-id momentum_v1 --updated-by risk-manager --reason "remove manual override" --dry-run` | Preview deactivating active allocation overrides. | Operators need reversible override cleanup. | read-only dry run | `AllocationOverridesRepository.get_active_override` | P1 |
| `atp controls allocation clear --strategy-id momentum_v1 --updated-by risk-manager --reason "remove manual override"` | Deactivate active allocation overrides and audit the change. | Clearing a manual control belongs next to setting it. | local-mutating | Add service method around `AllocationOverridesRepository.deactivate_override` with audit logging | P1 |
| `atp controls validate --config fixtures/controls.yaml` | Validate a controls YAML fixture without writing. | Legacy seed path needs safe preflight. | read-only | Shared schema for runtime controls, strategy toggles, and allocation overrides | P1 |
| `atp controls diff --config fixtures/controls.yaml` | Compare current controls to a proposed YAML fixture. | Operators need current -> proposed visibility before mutation. | read-only | `RuntimeControlService.get_controls_state` plus fixture parser | P1 |
| `atp controls seed --config fixtures/controls.yaml --dry-run` | Preview applying runtime controls, strategy toggles, and allocation overrides. | Replaces legacy `backtesting seed-controls` for control-state-only fixtures. | read-only | Split control-state-only logic from `handle_seed_controls` | P1 |
| `atp controls seed --config fixtures/controls.yaml --updated-by local-operator --reason "seed local controls"` | Apply a controls fixture with audit logging. | Useful for local/CI fixtures when scoped to controls only. | local-mutating | Services above, not direct repository writes | P2 |
| `atp controls audit-log --limit 20` | Show recent controls-related audit events. | Mutating controls need traceability. | read-only | `AuditLogRepository`, event types `TRADING_PAUSED`, `TRADING_RESUMED`, `TRADING_MODE_CHANGED`, `STRATEGY_ENABLED`, `STRATEGY_DISABLED`, allocation override events | P1 |
| `atp controls export --output artifacts/controls/current.json` | Emit controls snapshot artifact. | Controls are runtime inputs and should be reproducible. | read-only artifact output | `RuntimeControlService.get_controls_state` plus allocation override status | P1 |
| `atp controls verify-runtime-gates --strategy-id momentum_v1 --mode paper` | Confirm disabled/paused controls gate runtime job behavior. | Behavioral proof crosses scheduler/runtime. | cross-domain/runtime | Wrap existing operator passthrough/runtime tests or runtime job harness | P2 |

## 4. Testing Plan

Phase 0: help commands

```powershell
atp controls --help
atp controls state --help
atp controls pause --help
atp controls resume --help
atp controls mode --help
atp controls mode show --help
atp controls mode set --help
atp controls strategy --help
atp controls strategy list --help
atp controls strategy disable --help
atp controls strategy enable --help
atp controls allocation --help
atp controls allocation list --help
atp controls allocation status --help
atp controls allocation preview --help
atp controls allocation set --help
atp controls allocation clear --help
atp controls validate --help
atp controls diff --help
atp controls seed --help
atp controls audit-log --help
atp controls export --help
```

Phase 1: safe read-only commands

```powershell
atp controls state --json
atp controls mode show
atp controls strategy list --json
atp controls allocation list --json
atp controls allocation status
atp controls allocation preview --strategy-id momentum_v1 --allocation-pct 25
atp controls validate --config fixtures/controls.yaml
atp controls diff --config fixtures/controls.yaml
atp controls seed --config fixtures/controls.yaml --dry-run
atp controls allocation clear --strategy-id momentum_v1 --updated-by local-operator --reason "preview clear override" --dry-run
atp controls audit-log --limit 20
atp controls export --output artifacts/controls/current.json
```

Phase 2: local DB mutation commands

```powershell
atp controls pause --updated-by local-operator --reason "market data incident"
atp controls state --json
atp controls resume --updated-by local-operator --reason "market data recovered"
atp controls mode set --mode simulation --updated-by local-operator --reason "research replay"
atp controls mode set --mode paper --updated-by local-operator --reason "paper trading validation"
atp controls strategy disable --strategy-id momentum_v1 --updated-by local-operator --reason "operator pause"
atp controls strategy enable --strategy-id momentum_v1 --updated-by local-operator --reason "operator resume"
atp controls allocation set --strategy-id momentum_v1 --allocation-pct 25 --updated-by risk-manager --reason "risk rebalance"
atp controls allocation clear --strategy-id momentum_v1 --updated-by risk-manager --reason "remove manual override"
atp controls audit-log --limit 10
```

Phase 3: cross-domain/runtime commands

```powershell
atp controls verify-runtime-gates --strategy-id momentum_v1 --mode paper --updated-by local-operator --reason "verify disabled strategy is skipped"
atp controls verify-runtime-gates --mode simulation --updated-by local-operator --reason "verify mode gates runtime behavior"
```

Phase 4: broker/external commands

No controls-domain command should directly call broker or external APIs. Live gate, kill switch, emergency halt, and broker order cancellation belong in `safety` or `execution`. Controls commands may mutate local DB state that runtime/execution later honors, but they should not place orders or call broker clients.

## 5. Risks / Suspicious Wiring

- `src/autonomous_trading_platform/cli/commands/controls.py` does not exist.
- `src/autonomous_trading_platform/cli/main.py` does not import or register a `controls` domain.
- Legacy `backtesting seed-controls` mixes runtime controls, strategy governance fixture creation, strategy control states, and allocation overrides. That is too broad for a controls CLI.
- Legacy `backtesting seed-controls --clean` deletes existing strategy governance/control/allocation rows. A controls CLI should not expose destructive cleanup without an explicit separate fixture/testing command and strong confirmation.
- Legacy `backtesting seed-controls` writes through repositories directly instead of the audited services used by REST.
- Legacy `backtesting seed-controls` has no `--dry-run`.
- Legacy `backtesting seed-controls` has no explicit `--updated-by` or `--reason`; it uses fixture-oriented reasons like `controls seed`.
- `backtesting seed-controls --clean` deletes `StrategyGovernance`, all `StrategyControlState` rows, and active `AllocationOverrides` rows. It does NOT delete or reset `RuntimeControlState` (the global trading-enabled/paused/mode row). A controls CLI seed that applies `--clean` must clarify whether it also resets runtime control state.
- `_VALID_CONTROL_KEYS` (`trading_enabled`, `trading_paused`, `kill_switch_enabled`, `trading_mode`, `reason`) does not include `updated_by` or `updated_at`. A controls CLI seed command should accept actor/reason at the CLI level and set those fields explicitly.
- Legacy `_VALID_CONTROL_KEYS` includes `kill_switch_enabled`, but kill switch is a safety-domain concern.
- `RuntimeControlService.resume_trading()` releases the kill switch if it is active. A controls-domain `resume` command should avoid accidentally clearing a safety halt, or should refuse when kill switch is active and direct the operator to `atp safety disable-kill-switch`.
- `RuntimeControlService.activate_kill_switch()` records metadata source as `"api"` even when called outside REST; a future CLI wrapper would need source metadata support. Prefer keeping kill switch in `safety`.
- `RuntimeControlService.get_controls_state()` uses `get_or_create_global_state()`, so read-only CLI state inspection may create a default runtime control row.
- Trading mode mutation can move `paper -> live`. A CLI should require `--reason`, actor, and likely a safety preflight or confirmation for live mode.
- Allocation override mutation is audited by `StrategyAllocationService.override_allocation`, but clearing/deactivating overrides currently appears repository-level; a CLI clear command needs a service method with audit logging.
- Allocation override `preview` is needed because `set` can fail on aggregate allocation budget. Operators should see projected aggregate utilization before writes.
- Strategy enablement correctly validates governance state and audits through `StrategyControlService`; the CLI should use that service, not repository writes.
- Commands that emit current controls, diffs, and runtime verification should support JSON/artifact output.

## 6. Recommended Refactor / Extension

- Add `src/autonomous_trading_platform/cli/commands/controls.py` and register it in `src/autonomous_trading_platform/cli/main.py`.
- Add P0 commands first: `state`, `pause`, `resume`, `mode show`, `mode set`, `strategy enable`, `strategy disable`, `allocation preview`, and `allocation set`.
- Move or wrap `backtesting read-controls` as `controls state`.
- Split `backtesting seed-controls`: move control-state-only fixture application to `controls seed`; leave broad strategy/governance fixture creation in platform/testing tooling.
- Keep kill switch activation/deactivation in `safety`; controls should only display kill switch state and refuse unsafe resume behavior when a safety halt is active.
- Add `--dry-run` to fixture seed and allocation clear/set preview workflows.
- Add `--updated-by`, `--reason`, audit logging, JSON output, and artifact output for mutating commands.
- Add a service-backed allocation override clear operation so deactivation is audited.
- Add a safety gate or explicit confirmation for `mode set --mode live`.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp controls` | Missing | yes | Medium | Create `controls.py` and register the domain. |
| `atp controls state` | Missing | yes | Low | Implement from `RuntimeControlService.get_controls_state`; document default-row side effect or remove it. |
| `atp controls pause` | Missing | yes | Medium | Implement from `RuntimeControlService.pause_trading` with actor/reason. |
| `atp controls resume` | Missing | yes | High | Implement soft-resume only or refuse when kill switch is active. |
| `atp controls mode show` | Missing | yes | Low | Implement read-only mode inspection. |
| `atp controls mode set` | Missing | yes | High for live mode | Implement from `RuntimeControlService.update_trading_mode`; add live safety confirmation/preflight. |
| `atp controls strategy list` | Missing | yes | Low | Implement from controls state or strategy catalog. |
| `atp controls strategy disable` | Missing | yes | Medium | Implement from `StrategyControlService.set_enabled(False)`. |
| `atp controls strategy enable` | Missing | yes | Medium | Implement from `StrategyControlService.set_enabled(True)`. |
| `atp controls allocation list` | Missing | yes | Low | Implement from `StrategyAllocationService.get_allocations_for_active_strategies`. |
| `atp controls allocation status` | Missing | yes | Low | Implement from `StrategyAllocationService.get_aggregate_allocation_status`. |
| `atp controls allocation preview` | Missing | yes | Low | Add public preview/projection service method if needed. |
| `atp controls allocation set` | Missing | yes | Medium | Implement from `StrategyAllocationService.override_allocation`. |
| `atp controls allocation clear` | Missing | yes | Medium | Add audited service method before exposing CLI. |
| `atp controls validate` | Missing | yes | Low | Validate controls YAML before seed. |
| `atp controls diff` | Missing | yes | Low | Compare current state to proposed fixture. |
| `atp controls seed` | Missing; legacy exists as `backtesting seed-controls` | partial | High if broad/clean behavior is copied | Implement scoped, audited, dry-run fixture application only. |
| `atp controls audit-log` | Missing | yes | Low | Show controls-related audit events. |
| `atp controls export` | Missing | yes | Low | Emit controls snapshot artifact. |
| `atp controls verify-runtime-gates` | Missing | partial | Medium | Add explicit cross-domain wrapper or keep under runtime/platform. |
| `atp backtesting seed-controls` | Existing legacy | no | High | Split/migrate; do not copy broad fixture cleanup into controls. |
| `atp backtesting read-controls` | Existing legacy | no | Medium | Move/wrap as `controls state`. |
