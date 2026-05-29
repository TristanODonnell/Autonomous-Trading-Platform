# Governance CLI Domain Audit: `src/autonomous_trading_platform/cli/commands/governance.py`

Target CLI domain: `governance`

Target CLI file: `src/autonomous_trading_platform/cli/commands/governance.py`

Status: the target CLI file does not exist yet. This audit treats the current inventory as empty and proposes the governance CLI surface needed to make the existing governance, promotion, demotion, and health lifecycle logic operable from the command line.

## 1. Current CLI Inventory

No commands are currently registered in `src/autonomous_trading_platform/cli/commands/governance.py` because the file is absent.

| Command Path | Arguments / Options | Handler Function | Mutates State? | Calls External APIs? | Safe for Local Read-Only Testing? | Phase Classification |
|---|---|---|---:|---:|---:|---|
| `atp governance` | N/A | N/A | no | no | no | PLACEHOLDER |

Related governance behavior exists outside the target domain:

| Existing Command / Surface | Current Location | Notes | Recommended Treatment |
|---|---|---|---|
| `atp backtesting verify-auto-promotion` | `cli/commands/backtesting.py` | Seeds/validates auto-promotion behavior and persisted settings. It is governance behavior packaged under backtesting. | Move or wrap under `governance verify-auto-promotion`. |
| `atp backtesting verify-auto-demotion` | `cli/commands/backtesting.py` | Seeds/validates demotion behavior, including risk/control side effects. | Move or wrap under `governance verify-auto-demotion`; keep risk/control assertions explicit. |
| `atp backtesting verify-governance-allocation` | `cli/commands/backtesting.py` | Verifies governance allocation wiring, strategy controls, and allocation constraints. | Split or wrap: governance owns decision/source-of-truth checks; portfolio/controls own allocation effects. |
| Runtime governance cycle helpers | `application/scheduler/run_governance_promotion_cycle.py`, `run_governance_demotion_cycle.py`, `run_strategy_health_lifecycle_cycle.py` | Scheduler-oriented orchestration for promotion, demotion, and health lifecycle cycles. | Keep runtime orchestration under `runtime`; expose direct governance scan/run commands here. |

## 2. Domain Responsibility Check

The missing `governance.py` file belongs in the `governance` domain. Governance should own strategy lifecycle state, promotion/demotion decisions, governance evidence, review queues, supersession, and health lifecycle status. It should not own raw capital limits, live order routing, scheduler orchestration, or general platform smoke workflows.

| Command / Surface | Classification | Correct Domain? | Notes |
|---|---|---:|---|
| `atp governance` | correctly placed | yes | Should be created as the primary operator entrypoint for governance state and decision evidence. |
| `backtesting verify-auto-promotion` | should move to another domain | no | The behavior is governance verification, not backtesting. |
| `backtesting verify-auto-demotion` | should move / be duplicated elsewhere | partial | Governance owns state decisions; `risk` owns threshold pressure; `controls` owns strategy-disable/allocation override side effects. |
| `backtesting verify-governance-allocation` | should be duplicated/wrapped elsewhere | partial | Governance should verify decision source-of-truth; `portfolio` should verify allocation construction; `controls` should verify operator overrides. |
| Scheduler governance cycle triggers | should be duplicated/wrapped elsewhere | partial | `runtime` owns scheduler-like job triggering; `governance` should expose direct scan/run commands for operators and tests. |

## 3. Missing CLI Coverage

| Proposed Command Path | Purpose | Why It Belongs In Governance | Type | Implementation Target | Priority |
|---|---|---|---|---|---|
| `atp governance state list --state approved_paper --json` | List strategy governance records, optionally by lifecycle state. | Governance state is the domain source of truth for strategy lifecycle. | read-only | `GovernanceRepository.list_by_state`, `GovernanceRepository` query helpers | P0 |
| `atp governance state show --strategy-id momentum_v1 --json` | Show latest governance state for one strategy. | Operators need a direct lifecycle status lookup. | read-only | `GovernanceRepository.get_latest_by_strategy` | P0 |
| `atp governance transition --strategy-id momentum_v1 --to-state paper --updated-by risk-manager --actor-role risk_manager --source-run-id bt_20260501 --reason "passed paper gate" --dry-run` | Preview a manual lifecycle transition. | Transition validation is core governance behavior. | read-only if true preview is added | New preview wrapper around `StrategyGovernanceService.transition` validation path | P0 |
| `atp governance transition --strategy-id momentum_v1 --to-state paper --updated-by risk-manager --actor-role risk_manager --source-run-id bt_20260501 --reason "passed paper gate" --enforce` | Apply a manual lifecycle transition with evidence. | Governance owns strategy lifecycle mutation and audit evidence. | local-mutating | `StrategyGovernanceService.transition` | P0 |
| `atp governance rules list --json` | List active promotion rules. | Promotion criteria are governance policy. | read-only | `PromotionRulesRepository.get_all_active` | P0 |
| `atp governance rules show --from approved_research --to approved_paper --json` | Inspect transition criteria for one state pair. | Operators need explainability before promotion runs. | read-only | `PromotionRulesRepository.get_rules_for_transition` | P0 |
| `atp governance rules validate --config governance_rules.yaml` | Validate proposed rules without persisting them. | Keeps governance policy testable before mutation. | read-only | New validation service over promotion rule schema/repository constraints | P1 |
| `atp governance rules seed --config governance_rules.yaml --dry-run` | Preview rule seeding/upsert effects. | Governance policy setup belongs here. | read-only if implemented as true preview | New audited promotion rules service | P1 |
| `atp governance rules seed --config governance_rules.yaml --enforce` | Persist promotion rules. | Governance policy mutation should be explicit and audited. | local-mutating | `PromotionRulesRepository.insert`, preferably through new service | P1 |
| `atp governance rules deactivate --rule-id rule_paper_to_live_v1 --reason "superseded" --enforce` | Deactivate a promotion rule. | Rule lifecycle is governance policy management. | local-mutating | `PromotionRulesRepository.deactivate`, preferably through new service | P2 |
| `atp governance promotion scan --json` | Show auto-promotion candidates without applying transitions. | Candidate discovery is governance explainability. | read-only | `AutoPromotionService.scan` | P0 |
| `atp governance promotion run --actor governance-cli --enforce` | Execute auto-promotion decisions. | Auto-promotion mutates governance state and evidence. | local-mutating | `AutoPromotionService.run` | P0 |
| `atp governance demotion scan --json` | Show auto-demotion candidates without applying side effects. | Demotion candidate discovery belongs to governance, even when driven by risk signals. | read-only | `AutoDemotionService.scan` | P0 |
| `atp governance demotion run --actor governance-cli --dry-run --json` | Evaluate demotion decisions and emit evidence without applying full side effects. | Governance needs testable demotion behavior, but current dry-run still records evidence. | local-mutating unless changed | `AutoDemotionService.run(dry_run=True)` | P1 |
| `atp governance demotion run --actor governance-cli --enforce` | Apply demotion decisions and related controls. | Governance owns the lifecycle decision; side effects must be clearly reported. | cross-domain local mutation | `AutoDemotionService.run(dry_run=False)` | P0 |
| `atp governance health pending-review --json` | List strategies requiring health review. | Governance owns review workflow and lifecycle health. | read-only | `StrategyHealthLifecycleService.get_pending_reviews` | P0 |
| `atp governance health show --strategy-id momentum_v1 --json` | Show current health lifecycle status for a strategy. | Health lifecycle state informs governance decisions. | read-only | Health lifecycle repository/service query path | P1 |
| `atp governance health transitions --strategy-id momentum_v1 --json` | List health lifecycle transition history. | Health transition evidence is governance/audit material. | read-only | REST route equivalent / repository query | P1 |
| `atp governance health allocation-penalty --strategy-id momentum_v1 --json` | Show allocation penalty/scalar from health lifecycle. | Governance health can constrain allocation without being raw risk limits. | read-only | `StrategyHealthLifecycleService.get_allocation_penalty`, `get_allocation_scalar` | P1 |
| `atp governance health run --dry-run --json` | Preview health lifecycle actions. | Enables safe verification of health lifecycle rules. | read-only if preview is added | New preview wrapper around `StrategyHealthLifecycleService.run` | P1 |
| `atp governance health run --persist --trigger-source cli` | Apply health lifecycle cycle actions. | Governance owns persisted health transitions. | local-mutating / cross-domain if rebalance-linked | `StrategyHealthLifecycleService.run` or scheduler wrapper | P1 |
| `atp governance health clear-suspension --strategy-id momentum_v1 --actor risk-manager --reason "manual review passed" --enforce` | Clear a governance health suspension. | Suspension lifecycle is governance-controlled. | local-mutating | `StrategyHealthLifecycleService.clear_suspension` | P1 |
| `atp governance audit list --strategy-id momentum_v1 --event-type promotion --json` | Query governance decisions/evidence. | Evidence lookup is essential governance operability. | read-only | `GovernanceAuditService.list_decisions` | P0 |
| `atp governance audit show --governance-audit-id ga_123 --json` | Show one governance decision record. | Operators need inspectable decision detail. | read-only | `GovernanceAuditService.get_decision` | P0 |
| `atp governance audit chain --governance-audit-id ga_123 --json` | Show supersession chain for a decision. | Supersession is governance audit behavior. | read-only | `GovernanceAuditService.get_supersession_chain` | P1 |
| `atp governance audit supersede --governance-audit-id ga_123 --reason "corrected evidence" --actor admin --enforce` | Supersede an audit decision. | Governance owns audit decision corrections. | local-mutating | `GovernanceAuditService.supersede` | P2 |
| `atp governance verify-auto-promotion --settings fixtures/settings.json --json` | Move existing verification out of backtesting. | Tests promotion settings, rules, and decisions. | local-mutating fixture workflow | Existing `handle_verify_auto_promotion` logic refactored into service helper | P0 |
| `atp governance verify-auto-demotion --settings fixtures/settings.json --json` | Move existing demotion verification out of backtesting. | Tests demotion decisions and side effects. | cross-domain local mutation | Existing `handle_verify_auto_demotion` logic refactored into service helper | P0 |
| `atp governance verify-allocation-source-of-truth --controls fixtures/controls.json --settings fixtures/settings.json --total-capital 100000 --json` | Verify governance-controlled allocation source-of-truth wiring. | Governance owns lifecycle eligibility, but command should make portfolio/control dependencies explicit. | platform-level local fixture workflow | Existing `handle_verify_governance_allocation` split/wrapped | P1 |
| `atp governance export --strategy-id momentum_v1 --output artifacts/governance/momentum_v1.json` | Emit a governance evidence bundle. | Governance decisions should be portable and reviewable. | read-only artifact output | Governance repositories + audit service | P1 |

## 4. Testing Plan

### Phase 0: `--help` Commands

```powershell
python -m autonomous_trading_platform.cli --help
python -m autonomous_trading_platform.cli governance --help
python -m autonomous_trading_platform.cli governance state --help
python -m autonomous_trading_platform.cli governance rules --help
python -m autonomous_trading_platform.cli governance promotion --help
python -m autonomous_trading_platform.cli governance demotion --help
python -m autonomous_trading_platform.cli governance health --help
python -m autonomous_trading_platform.cli governance audit --help
```

### Phase 1: Safe Read-Only Commands

```powershell
python -m autonomous_trading_platform.cli governance state list --state approved_paper --json
python -m autonomous_trading_platform.cli governance state show --strategy-id momentum_v1 --json
python -m autonomous_trading_platform.cli governance rules list --json
python -m autonomous_trading_platform.cli governance rules show --from approved_research --to approved_paper --json
python -m autonomous_trading_platform.cli governance promotion scan --json
python -m autonomous_trading_platform.cli governance demotion scan --json
python -m autonomous_trading_platform.cli governance health pending-review --json
python -m autonomous_trading_platform.cli governance audit list --strategy-id momentum_v1 --page 1 --page-size 50 --json
python -m autonomous_trading_platform.cli governance audit show --governance-audit-id ga_123 --json
```

### Phase 2: Local DB Mutation Commands

```powershell
python -m autonomous_trading_platform.cli governance transition --strategy-id momentum_v1 --to-state paper --updated-by risk-manager --actor-role risk_manager --source-run-id bt_20260501 --reason "passed paper gate" --enforce --json
python -m autonomous_trading_platform.cli governance promotion run --actor governance-cli --enforce --json
python -m autonomous_trading_platform.cli governance demotion run --actor governance-cli --enforce --json
python -m autonomous_trading_platform.cli governance health clear-suspension --strategy-id momentum_v1 --actor risk-manager --reason "manual review passed" --enforce --json
python -m autonomous_trading_platform.cli governance rules seed --config fixtures/governance_rules.yaml --enforce --json
```

### Phase 3: Cross-Domain / Runtime Commands

```powershell
python -m autonomous_trading_platform.cli governance verify-auto-promotion --settings fixtures/operator_settings.json --json
python -m autonomous_trading_platform.cli governance verify-auto-demotion --settings fixtures/operator_settings.json --json
python -m autonomous_trading_platform.cli governance verify-allocation-source-of-truth --controls fixtures/strategy_controls.json --settings fixtures/operator_settings.json --total-capital 100000 --json
python -m autonomous_trading_platform.cli governance health run --persist --trigger-source cli --json
python -m autonomous_trading_platform.cli runtime trigger-governance-job --job-name strategy_auto_promotion_cycle --json
```

### Phase 4: Broker / External Commands

No governance command should call a broker or live external trading API directly. Governance may indirectly affect trading eligibility through controls, allocation overrides, or lifecycle state. Those commands should remain local mutations with explicit `--enforce`, JSON output, and audit evidence.

## 5. Risks / Suspicious Wiring

- `governance.py` is absent, so the intended domain is currently untestable through its own CLI.
- Existing governance verification is under `backtesting`, which is misleading because promotion, demotion, controls, allocation, and settings side effects are not backtest-only behavior.
- `AutoPromotionService.run(...)` mutates governance state and records promotion evidence, but there is no true `dry_run` path; `scan()` is the safe read-only alternative. The service checks `auto_promote_enabled` at the start of `run()` and returns `skipped_reason="auto_promote_disabled"` if the flag is false — the flag IS wired.
- `auto_promote_enabled` IS wired: confirmed by `AutoPromotionService.run()` line 172 and the `verify-auto-promotion` verification test. The `_GOVERNANCE_SETTING_WIRING` table in `backtesting.py` that shows `FLAG_NOT_WIRED` for this setting is stale and should not be trusted as a source of truth.
- `auto_demote_on_breach` IS wired: `AutoDemotionService.run()` checks the flag at line 226 and returns `skipped_reason="auto_demote_disabled"` if false. Same stale classification exists in `backtesting.py`.
- `AutoDemotionService.run(dry_run=True)` returns early with `skipped_reason="dry_run"` before running actual demotion evaluation. It does not apply demotion state changes. However, it is still local-mutating if `auto_demote_on_breach` is false first — that check runs before the dry_run branch. Confirm idempotency in any fixture that calls the demotion service.
- `StrategyHealthLifecycleService` (Rec 6.3) exists with: a SUSPENDED lifecycle status, anti-flapping cooldown logic, allocation penalty/scalar computation, operator recovery via `clear_suspension`, and a `strategy_health_lifecycle_transitions` SOR table. Health lifecycle states are separate from governance lifecycle states (`approved_paper`, `approved_live`, etc.) but can constrain allocation without changing the governance tier. Governance CLI should expose both lifecycle views distinctly.
- Promotion/demotion services may initialize default operator settings through `get_or_create_default()`, so even nominal governance runs should be audited for implicit local mutation.
- Auto-demotion can disable strategy controls, zero allocation overrides, and freeze trading when severity warrants it. The CLI must make those side effects explicit before enforcement.
- `StrategyGovernanceService.transition(...)` correctly performs role/state validation and evidence recording, but a CLI wrapper must require `--updated-by`, `--actor-role`, `--reason`, and `--source-run-id` for capital-bearing transitions.
- Promotion rules currently have repository methods for insert/deactivate, but there is no obvious audited rules management service. A CLI should not expose direct mutation without an audit wrapper.
- Active promotion rule lookup can fail if duplicate active rules exist for one transition. Add `rules validate` before `rules seed`.
- Health lifecycle overlaps with `risk` when drawdown or breach thresholds are involved. Governance should expose review, suspension, health state, and allocation penalty; raw risk limits stay in `risk`.
- Governance commands should emit JSON and artifact bundles because decision evidence is central to operator review and auditability.

## 6. Recommended Refactor / Extension

Add a new `governance.py` CLI domain. Start with read-only `state`, `rules`, `promotion scan`, `demotion scan`, `health pending-review`, and `audit` commands, then add guarded mutation commands with `--enforce`, JSON output, and audit logging.

Move or wrap the existing `backtesting verify-auto-promotion`, `backtesting verify-auto-demotion`, and governance allocation verification into governance-specific verification commands. Keep runtime scheduler triggers in the `runtime` domain, but expose direct governance scan/run commands here for local testing and operator workflows.

Add true dry-run/preview support where it is currently missing, especially for manual transitions, auto-promotion, and health lifecycle runs. Treat auto-demotion `dry_run=True` as local-mutating until it stops writing audit evidence.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `atp governance` | missing | yes | High | Create domain file and register command group. |
| `atp governance state list/show` | missing | yes | Low | Add P0 read-only state inspection. |
| `atp governance transition` | missing | yes | High | Add guarded lifecycle mutation with `--dry-run` preview and `--enforce`. |
| `atp governance rules list/show` | missing | yes | Low | Add P0 read-only promotion rule inspection. |
| `atp governance rules seed/deactivate` | missing | yes | Medium | Add audited rules service before exposing mutation. |
| `atp governance promotion scan/run` | missing | yes | Medium | Add scan first; require `--enforce` for run. |
| `atp governance demotion scan/run` | missing | yes | High | Add scan first; make control/allocation/freeze side effects explicit. |
| `atp governance health *` | missing | yes | Medium | Add pending-review and penalty reads before persisted run/clear commands. |
| `atp governance audit list/show/chain/supersede` | missing | yes | Medium | Add audit reads first; guard supersession mutation. |
| `atp governance verify-auto-promotion` | currently under `backtesting` | yes | Medium | Move or wrap existing verification helper. |
| `atp governance verify-auto-demotion` | currently under `backtesting` | yes | High | Move or wrap and report cross-domain side effects. |
| `atp governance verify-allocation-source-of-truth` | currently under `backtesting` | partial | Medium | Split governance decision checks from portfolio/control allocation checks. |
