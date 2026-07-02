# Platform Settings Reference

Two places control platform behaviour. Read this before touching either.

---

## 1. Code defaults — the fallback

**File:** `src/autonomous_trading_platform/storage/sor/repositories/core/operator_settings_repository.py`
**Method:** `OperatorSettingsRepository.get_or_create_default()`

These values are written to the DB the first time any service calls `get_or_create_default()` on a
fresh database. They are not read again after that — once the row exists, only explicit updates via
the settings API or a replay fixture can change them.

---

## 2. Replay fixture override — what the YAML seeds

**File:** `fixtures/platform/replays/long/full_year_demo.yaml` (or any replay fixture)
**Block:** `initial_state.settings`
**Code that applies it:** `src/autonomous_trading_platform/application/services/platform_replay/initial_state_hooks.py`
**Method:** `_apply_operator_settings()`

The fixture patch is applied before tick 1. Only fields listed in `_apply_operator_settings.field_map`
can be set from the YAML. Everything else uses the code default.

---

## Full settings table

| Field | Code default | What it controls | Patchable from YAML |
|-------|-------------|-----------------|---------------------|
| `risk_tolerance` | `"medium"` | Label used in UI and audit log. `"low"` / `"medium"` / `"high"`. Has no direct mechanical effect — it's context for operators and notifications. | yes |
| `max_drawdown_limit` | `0.10` (10%) | **Portfolio-level drawdown gate.** If the portfolio drops more than this % from its peak, `portfolio_drawdown_action` fires. Evaluated daily by `PortfolioDrawdownGovernanceService`. | yes |
| `max_strategy_drawdown` | `0.12` (12%) | **Per-strategy drawdown gate.** If a strategy's own P&L drops more than this % from its peak, `AutoDemotionService` flags it for demotion. Evaluated daily. | yes |
| `per_strategy_cap` | `0.25` (25%) | **Concentration cap.** `QualityBasedReallocationService` will not allocate more than this fraction of capital to any single strategy regardless of its quality score. | yes |
| `target_portfolio_volatility` | `0.15` (15%) | **Volatility target for risk budgeting.** `RiskBudgetingService` scales positions to hit this annualised vol. Set higher for more aggressive sizing, lower for defensive. | yes |
| `auto_promote_enabled` | `False` | **Autonomous promotion gate.** If `True`, `AutoPromotionService` can move strategies from `approved_research` → `approved_for_paper_trading` without operator sign-off. Set `False` in live to require human approval for each promotion. | yes |
| `auto_rebalance_enabled` | `False` | **Autonomous reallocation gate.** If `True`, `QualityBasedReallocationService` shifts capital between approved strategies based on quality scores. If `False`, starting allocations are static. **Must be `True` for the allocation story to be visible.** | yes |
| `rebalance_frequency` | `"weekly"` | **Rebalance cadence label.** Used by `QualityBasedReallocationService` to decide how often to attempt a rebalance. Values: `"daily"`, `"weekly"`, `"monthly"`. `"daily"` causes churn; `"weekly"` is the right level for a month-long signal horizon. | yes |
| `min_rebalance_interval_hours` | `24.0` | **Hard interval guard (hours).** Even if `rebalance_frequency` says weekly, a second rebalance cannot fire within this many hours of the last one. Set to `168` (= 7 days) to enforce true weekly cadence. | yes |
| `min_allocation_change_pct` | `0.01` (1%) | **Hysteresis dead-band.** A rebalance only executes if at least one strategy's target allocation differs from its current allocation by more than this amount. Prevents micro-churn from floating-point quality score noise. | yes |
| `auto_demote_on_breach` | `True` | Whether `AutoDemotionService` actually executes demotions or only flags candidates. `True` = execute; `False` = dry-run (observe only). | no — set in code default |
| `min_sharpe_for_promotion` | `1.5` | Legacy field — superseded by `PromotionRules` rows in the DB. Still read as a fallback if no promotion rule exists for a transition. | no |
| `min_paper_trading_period_days` | `30` | Legacy field — superseded by `PromotionRules`. Minimum days a strategy must be in paper trading before promotion is considered. | no |
| `portfolio_max_drawdown_pct` | `0.15` (15%) | Alias used by `PortfolioDrawdownGovernanceService` for the drawdown ladder trigger. If both this and `max_drawdown_limit` are set, the ladder uses this field. | no — set in code default |
| `portfolio_drawdown_action` | `"pause_new_trading"` | What the portfolio drawdown ladder does at breach. Options: `"pause_new_trading"`, `"reduce_positions"`, `"halt_all"`. | no — set in code default |
| `portfolio_drawdown_recovery_mode` | `"manual_resume_required"` | How the system recovers after a drawdown breach. `"manual_resume_required"` = operator must call the resume endpoint. `"auto_recover"` = resumes automatically once drawdown heals. | no — set in code default |
| `notify_drawdown_alerts` | `True` | Emit audit-log entries when drawdown thresholds are crossed. | no |
| `notify_strategy_promotion_events` | `True` | Emit audit-log entries on every governance promotion. | no |
| `notify_strategy_demotion_events` | `True` | Emit audit-log entries on every governance demotion. | no |
| `notify_allocation_rebalance_events` | `True` | Emit audit-log entries when `QualityBasedReallocationService` executes a rebalance. | no |
| `notify_pipeline_failures` | `True` | Emit audit-log entries when ingestion or research pipeline jobs fail. | no |

---

## Universe and trading symbol pools

**Where:** `fixtures/platform/replays/long/full_year_demo.yaml` → `platform_replay.symbols`

The `symbols` list is the **data universe** — the set of tickers for which ingestion pulls market bars.
This is NOT the trading universe. The trading universe is a subset selected monthly by `run_universe_rotation`
using the prior month's performance data. On tick 1 the monthly cadence fires immediately (because
`last_run == None`), so the trading universe is dynamically selected from the pool before the first trade.

Adding more symbols gives universe rotation more to work with and produces a more interesting
rotation story. Minimum meaningful pool size: ~10 symbols. The current storytelling config uses 13.

---

## Where other thresholds live

| What | Where |
|------|-------|
| Promotion eligibility criteria (Sharpe, trade count, days tested) | `promotion_rules` DB table — populated by migration or API |
| Drawdown ladder rungs (WARNING/PROBATION/SUSPENDED/BREACHED) | `drawdown_governance_ladder_state` DB table |
| Slippage model parameters | `operator_settings.slippage_model` + `SlippageCalibrationSnapshot` |
| Sector concentration limits | `RiskLimitConfig` in code (`safety/risk_limit_config.py`) |
| Kill switch state | `kill_switch_state` DB table (singleton) — do not edit directly |

---

## To change settings in a replay

Edit `initial_state.settings` in the fixture YAML. Only fields in the table above marked
"yes" in the **Patchable from YAML** column take effect. All others require a code change to
`operator_settings_repository.py:get_or_create_default()` or a DB migration.

## To change settings in the live system

Use the REST API: `PATCH /api/v1/settings` (requires operator JWT). Changes are applied
immediately and written to the `operator_settings` DB row.
