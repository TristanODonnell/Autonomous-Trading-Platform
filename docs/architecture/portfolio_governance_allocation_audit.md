# Portfolio, Governance & Capital Allocation Architecture Audit

**Date:** 2026-05-20
**Branch:** strategy-simulator-experiments-features-updates
**Scope:** Multi-strategy portfolio orchestration, capital allocation, governance lifecycle, strategy health, runtime/research consistency, operational safety
**Methodology:** Full codebase exploration covering all related services, models, contracts, scheduler cycles, and API endpoints

---

## Table of Contents

1. [Architecture Inventory](#1-architecture-inventory)
2. [Gap Analysis by Severity](#2-gap-analysis-by-severity)
3. [Operational Failure Scenario Matrix](#3-operational-failure-scenario-matrix)
4. [Portfolio Intelligence Maturity Assessment](#4-portfolio-intelligence-maturity-assessment)
5. [Prioritized Remediation Roadmap](#5-prioritized-remediation-roadmap)
6. [Recommendations for Institutional-Grade Orchestration](#6-recommendations-for-institutional-grade-orchestration)

---

## 1. Architecture Inventory

### 1.1 Governance System

**State Machine**

```
GovernanceState (StrEnum):
  proposed → approved_research → approved_paper → approved_live → retired
                              ↘               ↘              ↘
                            rejected        rejected       rejected
                               ↓
                           proposed (re-submission allowed)
```

**Transition authorization matrix:**
| Target State | Required Role |
|---|---|
| approved_research | researcher, admin |
| approved_paper | risk_manager, admin |
| approved_live | admin only |
| retired | operator, risk_manager, admin |

**Promotion criteria gate (`PromotionRules` table):**
- Per-transition thresholds: `min_sharpe`, `max_drawdown`, `min_days_tested`, `min_trade_count`, `min_cagr`, `min_win_rate`
- All fields nullable — a null threshold is skipped (not enforced)
- Source: `storage/sor/models/promotion_rules.py`

**Key services:**
- `StrategyGovernanceService.transition()` — validates role, state legality, promotion criteria, writes audit event
- `AutoPromotionService.run()` — scans candidates, calls `transition()` with system-assigned roles
- `StrategyGovernanceService._assert_promotion_criteria_met()` — metrics lookup chain: source_run_id → latest by strategy_id → metrics_json fallback

---

### 1.2 Allocation System

**Allocation policy model (`CapitalAllocationPolicies`):**
- Keyed by `(approval_status, performance_tier)` with tier optional
- Fields: `max_pct_of_capital`, `max_position_size_usd`, `max_drawdown_allowed`
- Fallback: if tier-specific policy not found, re-queries with `performance_tier=None`

**Allocation override model (`AllocationOverrides`):**
- Per-strategy soft-delete with expiration (`expires_at`)
- `overridden_by` field distinguishes manual ("actor name") from automatic ("auto_rebalance")
- Override fields take precedence over policy where not null

**Allocation resolution chain (`PortfolioEngine.get_allocation()`):**
1. Assert strategy in `{approved_research, approved_paper, approved_live}` (raises `AllocationDeniedError`)
2. Load policy by `(approval_status, performance_tier)` with tier fallback
3. Load active non-expired override for strategy_id
4. Merge: override fields override policy fields where not null
5. Compute `allocated_capital_usd = max_pct * total_capital`
6. Guard: `allocated_capital_usd <= total_capital` (raises `InsufficientCapitalError`)

**Quality-based auto-rebalance (`QualityBasedReallocationService`):**

Quality score formula:
```python
score = 1.0
score += max(sharpe, -1) * 0.40
score += total_return * 1.50
score += win_rate * 0.40
score += min(trade_count / 100, 0.25)
score -= abs(max_drawdown) * 2.00
score = max(score, 0.01)
```

Bucketing: disabled → 0%, manual override → capped by policy, variable → quality-weighted from remaining budget.

---

### 1.3 Portfolio Construction

**`PortfolioConstructionService.generate_order_intents()` pipeline:**
1. For each signal: compute `target_qty` via `PositionSizer`
2. Delta from current positions
3. For each non-zero delta: pre-trade risk gate (`PreTradeRiskService.assert_order_allowed()`)
4. Yield `OrderIntent`

**`PositionSizer.compute_quantity()` chain:**
1. `PortfolioEngine.get_allocation()` → `allocated_capital_usd`
2. `target_notional = allocated_capital_usd * capital_fraction`
3. If `vol_scalar`: `target_notional *= vol_scalar` (clamp to (0, 1])
4. Apply `max_position_size_usd` cap (policy)
5. Apply `max_symbol_exposure_usd` cap (settings)
6. If below `min_notional_usd`: return 0
7. `quantity = floor(target_notional / current_price)`

**Combined scalar logic:**
- Vol scalar and Sharpe scalar are multiplied if both available, clamped to `(0, 1]`
- Either can be `None` — uses only the available scalar
- Neither available — no scaling (full notional)

---

### 1.4 Risk Management

**Pre-trade gates (`PreTradeRiskService`):**
- `GrossExposureLimitExceededError` — projected gross exceeds limit
- `SymbolExposureLimitExceededError` — projected per-symbol exceeds limit
- `DailyNotionalLimitExceededError` — daily turnover exceeds limit
- Reserved cash check for BUYs

**Post-execution risk snapshots (`RiskSnapshotService`):**
- `gross_exposure = Σ|position.market_value|`
- `net_exposure = Σposition.market_value`
- `leverage = gross_exposure / equity`
- `utilization = metric / limit` per limit dimension
- `is_blocked = len(block_reasons) > 0`

**`RiskLimitConfig`:** `max_gross_exposure`, `max_net_exposure`, `max_leverage` — all nullable

---

### 1.5 Observability & Audit

**Audit log model (`AuditLogRow`):**
- Fields: `event_id`, `run_id`, `event_type`, `component`, `event_timestamp`, `message`, `metadata` (JSONB)
- Events: governance transitions, allocation overrides, rebalance completions/skips, auto-promotion runs

**Allocation decision traceability (`QualityReallocationResult`):**
- `before_allocation`, `after_allocation` per strategy
- `proposals` list with per-strategy reason
- `quality_metrics` snapshot used for scoring
- `active_policies`, `allocation_overrides` context at execution time

---

### 1.6 Simulation / Replay Paths

**Backtest orchestrator (`BacktestTradingCycleOrchestrator`):**
- Uses same `PortfolioEngine`, same `PositionSizer`, same pre-trade risk gates
- `BacktestFillSimulator` replaces live broker submission
- `StubRiskStateReader`, `StubOrderActivityReader` replace live broker state readers
- Run-scoped `strategy_id` prefix (`backtest_{run_id}_...`) prevents state collision

**Determinism mechanisms:**
- `OrderIntent.intent_id = uuid5(run_id, strategy_id, bar_timestamp, symbol, side, qty)`
- Position sizing is pure function of prices + allocation (same inputs → same output)
- Risk snapshots computed from snapshots (same positions + cash → same snapshot)

---

## 2. Gap Analysis by Severity

### Severity Legend
- **P0 — Critical:** Can cause incorrect trade sizing, silent capital loss, or undetected governance bypass in production
- **P1 — High:** Significant operational risk or portfolio integrity risk under plausible production conditions
- **P2 — Medium:** Missing intelligence that reduces portfolio quality or operational observability
- **P3 — Low:** Improvements for institutional maturity; not immediately risky

---

### FINDING-01 — No Auto-Demotion or Performance Breach Response
**Severity:** P0
**Affected components:** `StrategyGovernanceService`, `AutoPromotionService`, `QualityBasedReallocationService`

**Description:**
The governance FSM only promotes upward or moves a strategy to `retired`/`rejected` via explicit operator action. There is no automated path that demotes a live strategy to paper, or paper to research, when its realized performance breaches thresholds. The `AutoPromotionService` only runs the `scan()` → `transition()` path in the promotion direction. Nothing triggers demotion based on live performance degradation.

**Why it matters:**
A strategy that passes promotion criteria at time T can degrade substantially afterward — Sharpe collapses, win rate falls, drawdown grows — while still holding `approved_live` governance state. The allocation system and portfolio engine treat governance state as a signal of fitness. If state is stale, the strategy continues receiving full capital allocation despite deteriorating performance. Quality-based rebalancing does reduce its allocation weight, but it does not revoke its right to trade (governance gate) and does not trigger operator notification that demotion should be considered.

**Production consequences:**
- Strategy continues trading with `approved_live` capital even as it loses money
- Drawdown cap (`max_drawdown_allowed`) in policy is a pre-execution position-size cap, not a realized drawdown circuit breaker
- Auto-rebalance reduces allocation weight but the minimum floor is 0.01 — strategy is never automatically zeroed
- Operator must notice degradation from dashboards and manually intervene

**Recommended remediation:**
Implement `AutoDemotionService` mirroring `AutoPromotionService`:
- Scan `approved_live` and `approved_paper` strategies for realized metric breach
- Breach criteria: realized drawdown > `max_drawdown_allowed` (from policy), Sharpe < 0 over trailing N bars, win_rate below threshold over trailing N trades
- On breach: transition to previous state (live → paper, paper → research) with `actor_role="system_risk"` and reason attached to audit log
- Notify via `STRATEGY_DEMOTION_EVENT`
- Requires adding a `lookback_window` to `PromotionRules` to distinguish "ever achieved" from "currently maintaining"

---

### FINDING-02 — No Cross-Strategy Symbol Concentration Limit
**Severity:** P0
**Affected components:** `PreTradeRiskService`, `PortfolioConstructionService`, `RiskLimitConfig`

**Description:**
Pre-trade risk checks enforce per-order gross exposure and per-symbol exposure limits, but these are checked in the context of a single strategy execution. When multiple strategies independently generate BUY signals for the same symbol, each passes its own pre-trade check. The portfolio ends up with aggregate exposure to that symbol far exceeding what any single strategy's limit would allow.

**Why it matters:**
If `MomentumStrategy` buys AAPL up to its symbol exposure limit, and `MACDStrategy` independently buys AAPL up to its own symbol exposure limit, the portfolio's real AAPL exposure is `N × per_strategy_limit`. There is no cross-strategy accumulator. The `RiskSnapshotService` does compute post-execution aggregate gross/net exposure from `PositionSnapshot`, but this is a diagnostic — not a pre-trade gate.

**Production consequences:**
- In a bullish momentum regime, all trend-following strategies converge on the same high-momentum names
- Portfolio ends up heavily concentrated in a handful of symbols despite governance documents indicating controlled concentration
- A gap down or earnings miss on a concentrated name creates correlated drawdown across all strategies simultaneously
- Risk snapshot `is_blocked` flag is post-execution and doesn't prevent the orders from being placed

**Recommended remediation:**
Introduce a `PortfolioRiskStateReader` that aggregates position state across all active strategies (not per-strategy):
- Add `max_portfolio_symbol_exposure_usd` and `max_portfolio_symbol_pct` to `RiskLimitConfig`
- In `PreTradeRiskService.assert_order_allowed()`: query aggregate symbol exposure across all strategies before the order, not just the requesting strategy's exposure
- Requires a portfolio-level position store or real-time aggregation over `PositionSnapshot` rows

---

### FINDING-03 — Kill Switch Is In-Memory and Not Persisted
**Severity:** P0
**Affected components:** `KillSwitchService`, any consumers of `assert_not_enabled()`

**Description:**
`KillSwitchService` stores `_enabled`, `_reason`, `_updated_by`, `_updated_at` as instance-level Python attributes. There is no database write, no SOR record, and no persistent state. If the API pod restarts (crash, OOM kill, deploy), the kill switch is silently reset to disabled.

**Why it matters:**
A kill switch is a safety control for halting all trading in an emergency. Its entire value proposition is that it persists across failures and restarts. An in-memory kill switch provides a false sense of safety: an operator enables the kill switch, the pod crashes 30 seconds later due to unrelated reasons, a new pod starts with kill switch disabled, trading resumes.

**Production consequences:**
- Kill switch enabled during a flash crash or data feed anomaly is silently cleared on pod restart
- Orchestration layer (Airflow DAG) may restart trading cycles while operator believes the kill switch is active
- No audit trail of when kill switch was enabled vs. when it was implicitly cleared by restart
- Particularly dangerous in containerized/Kubernetes environments with automatic pod restarts

**Recommended remediation:**
Persist kill switch state to a dedicated SOR table (`KillSwitchState`):
- Columns: `is_enabled`, `reason`, `updated_by`, `updated_at`, `cleared_at`, `cleared_by`
- `KillSwitchService` writes to this table on `enable()` and `disable()`
- On service startup, reads current state from SOR
- All scheduler cycle entrypoints check kill switch from SOR before starting any trading work
- Add a startup audit event: `KILL_SWITCH_STATE_LOADED` with the resolved state

---

### FINDING-04 — Total Capital Allocation Can Silently Exceed 100%
**Severity:** P1
**Affected components:** `PortfolioEngine.get_allocation()`, `QualityBasedReallocationService`, `StrategyAllocationService`

**Description:**
`PortfolioEngine.get_allocation()` computes `allocated_capital_usd = max_pct * total_capital` per strategy independently. If three strategies each hold `max_pct_of_capital = 0.40` (40%), the total allocation across strategies is 120% of capital. The only guard is `allocated_capital_usd <= total_capital` per individual strategy — not aggregate.

`QualityBasedReallocationService` does enforce a total-budget constraint through its weighted distribution algorithm. However, the rebalancer only runs when `auto_rebalance_enabled = True`. When auto-rebalance is off, or when manual overrides are applied, the budget constraint is not enforced.

**Why it matters:**
Manual allocation overrides set via `PUT /strategies/{id}/allocation` are validated only to the range `[0, 100]` per strategy. An operator can override strategy A to 60% and strategy B to 60% — the system will accept both, and `PortfolioEngine` will allocate 60% of capital to each independently. When position sizing runs for each strategy, each strategy sizes positions as if it owns 60% of capital, effectively deploying 120% of capital into the market.

**Production consequences:**
- Platform unknowingly operates at 2x intended leverage during manual override periods
- `RiskLimitConfig.max_leverage` may catch this post-execution, but margin calls or broker rejection may occur first
- No warning shown to operator when the sum of active overrides exceeds 100%

**Recommended remediation:**
Add a portfolio-level budget validation to `StrategyAllocationService.override_allocation()`:
- Before writing the new override, sum all active `max_pct_of_capital` values across all strategies (including the new one)
- Raise `AllocationBudgetExceededError` if sum > 1.0 (or configurable threshold)
- Alternatively: soft-warn but allow, recording an `ALLOCATION_BUDGET_EXCEEDED` audit event
- `PortfolioEngine` should expose a `get_aggregate_allocation_pct()` method for monitoring

---

### FINDING-05 — Quality Score Uses Historical Metrics, Not Realized Runtime Metrics
**Severity:** P1
**Affected components:** `QualityBasedReallocationService`, `AutoPromotionService`, `MetricsSummary`

**Description:**
Both the quality score computation and the promotion criteria check load metrics from `MetricsSummary`, which is a snapshot of simulation/backtest results. The quality score formula operates on `total_return`, `sharpe`, `win_rate`, `trade_count`, `max_drawdown` — but these are the metrics from the strategy's research simulation, not from its actual live trading performance.

**Why it matters:**
The reallocation service is supposed to adaptively shift capital toward better-performing strategies. But it ranks strategies by their simulation-era performance rather than by how they are actually performing in the current market regime. A strategy that looked excellent in backtests from 2020–2022 may be underperforming significantly in live trading, yet it receives high quality weight because its `MetricsSummary` records the historical simulation scores.

**Production consequences:**
- Capital is misallocated: poorly-performing live strategies retain high weight due to legacy simulation scores
- Good live performers with conservative backtest scores receive less capital than they deserve
- Regime shifts (e.g., low-volatility to high-volatility) cause the quality model to become increasingly stale
- The rebalancer provides the illusion of adaptive allocation without the substance

**Recommended remediation:**
Introduce a `LivePerformanceMetricsService` that computes rolling live metrics from `PositionSnapshot` + `CashSnapshot` history:
- Rolling Sharpe (trailing 20/60 bars)
- Realized drawdown (peak-to-trough on live equity curve)
- Live win rate from fills
- Days since last profitable day
- Quality score should blend: `alpha * live_score + (1 - alpha) * backtest_score` where `alpha` increases with live trading history depth

---

### FINDING-06 — No Rebalance Frequency Guard / Turnover Limit
**Severity:** P1
**Affected components:** `QualityBasedReallocationService`, `run_allocation_rebalance_cycle.py`, `StrategyAllocationService`

**Description:**
The rebalance cycle is triggered independently and there is no guard on rebalance frequency. The cycle can be triggered as often as the Airflow schedule allows. Each rebalance writes new auto-generated overrides that update every strategy's allocation percentage. There is no minimum time-between-rebalances, no minimum change threshold (hysteresis), and no turnover cost model.

**Why it matters:**
Frequent rebalancing creates order churn: position sizers re-read allocations each cycle and generate new order intents based on updated target notionals. If allocations shift from 15% to 16% and back to 15% across consecutive rebalance cycles, position sizes will oscillate, generating unnecessary buys and sells. Each round-trip incurs transaction costs (spreads, commissions, market impact). For a 10-strategy portfolio on 5-minute cycles, this can be significant.

**Production consequences:**
- Unnecessary transaction costs from allocation-driven order churn
- Quality-score sensitivity to noisy short-term metrics causes oscillation
- If two rebalance cycles overlap (slow execution), concurrent writes to `AllocationOverrides` may create race conditions where both cycles write new overrides and then both deactivate the other's work

**Recommended remediation:**
Add rebalance stability controls:
- `min_rebalance_interval_hours` in operator settings — skip rebalance if last rebalance was within this window
- Minimum allocation change threshold: only rewrite override if `|new_pct - current_pct| > delta_threshold` (e.g., 1%)
- Record last rebalance timestamp in SOR
- Optionally: turnover penalty term in quality score: `score -= turnover_cost * expected_monthly_turnover`

---

### FINDING-07 — Governance Promotion Criteria Can Be Bypassed via Null Thresholds
**Severity:** P1
**Affected components:** `StrategyGovernanceService._assert_promotion_criteria_met()`, `PromotionRules`

**Description:**
`PromotionRules` fields (`min_sharpe`, `max_drawdown`, `min_days_tested`, `min_trade_count`, `min_cagr`, `min_win_rate`) are all nullable. In `_assert_promotion_criteria_met()`, a null threshold is skipped — the criterion is not checked. If a promotion rule row has all nulls (or is missing), any strategy can be promoted through any state transition without evidence of performance.

**Why it matters:**
The intent of the promotion criteria gate is to ensure a strategy has demonstrated fitness before receiving live capital. Null thresholds silently disable this gate. An operator configuring a new promotion rule row might leave fields null intending to configure them later. Meanwhile, `AutoPromotionService` scans and promotes any eligible candidate — including ones that pass because all thresholds are null.

**Production consequences:**
- A strategy can be promoted to `approved_live` with zero trading history, one simulation run with a high Sharpe from a 2-day window, or no criteria whatsoever
- Audit log will show the promotion as succeeded with no indication that criteria were skipped
- Particularly dangerous for `AutoPromotionService` which promotes with `admin` role without a human in the loop

**Recommended remediation:**
- Define a minimum set of non-nullable required criteria for each transition (at minimum: `min_days_tested`, `min_trade_count`)
- Add database-level `CHECK` constraints on key thresholds: `CHECK (min_days_tested >= 30)` for paper → live
- In `_assert_promotion_criteria_met()`: if the promotion target is `approved_live`, treat null `min_sharpe`, `min_days_tested`, `min_trade_count` as missing rules and raise an error
- Log which criteria were skipped (null) in the audit event metadata

---

### FINDING-08 — No Multi-Strategy Position Netting / Conflict Resolution
**Severity:** P1
**Affected components:** `PortfolioConstructionService`, execution layer

**Description:**
When multiple strategies generate conflicting signals for the same symbol (e.g., `MomentumStrategy` generates BUY AAPL, `MeanReversionStrategy` generates SELL AAPL), the portfolio construction layer handles each strategy independently. Each strategy's `generate_order_intents()` is called in isolation and both order intents are submitted. The net result may be a wash trade (buy and sell the same symbol in the same cycle), or partially offsetting positions held simultaneously.

**Why it matters:**
At the portfolio level, a simultaneous long from strategy A and a short from strategy B on the same symbol results in:
- Double commission spend (two trades instead of net zero)
- Possible margin/regulatory complications if positions are in a non-margin-capable account
- Risk snapshot `net_exposure` looks smaller than reality (netting hides gross exposure from individual strategies)
- The pre-trade risk gate checks each order independently; the combined risk of opposing positions crossing is not analyzed

**Production consequences:**
- Unnecessary transaction costs from wash trades
- Portfolio exposure metrics misleading if opposing positions held simultaneously
- In live Alpaca integration: both orders submitted to broker; execution depends on fill order

**Recommended remediation:**
Implement a signal aggregation layer above `PortfolioConstructionService`:
- `PortfolioSignalAggregator` collects signals from all active strategies per symbol
- Net signal logic: sum of direction-weighted sizes; if net is near zero, suppress both
- Configurable aggregation policy: `conservative` (suppress conflicts), `dominant` (use signal from highest-conviction strategy), `proportional` (scale by allocation weight)
- This aggregation layer is logically above the executor; it reconciles at portfolio level before any order intents are generated

---

### FINDING-09 — Performance Decay Detection Is Absent
**Severity:** P2
**Affected components:** `AutoPromotionService`, governance layer, health monitoring

**Description:**
There is no service or mechanism that detects gradual strategy performance degradation over time. The governance system checks criteria at promotion time (static snapshot). Quality-based rebalancing re-scores strategies each cycle but does not flag when a strategy's score crosses a health threshold in a downward direction. There is no concept of "strategy health status" separate from governance state.

**Why it matters:**
Strategy performance can erode gradually due to regime change, factor crowding, or model drift. A strategy that passed promotion criteria 6 months ago may now have consistently negative alpha. Without trend monitoring, the system has no mechanism to:
- Alert operators to deteriorating strategies before they breach hard drawdown limits
- Reduce allocation proactively as confidence in a strategy wanes
- Distinguish "temporarily underperforming" from "structurally broken"

**Production consequences:**
- Silent capital bleed until drawdown limit is reached or operator notices manually
- No early-warning system means operators are reactive rather than proactive
- Quality rebalancer reduces weight on bad strategies but at a static snapshot level — no momentum in the scoring

**Recommended remediation:**
Implement `StrategyHealthMonitor` as a standalone service:
- Track quality score over time (persist `QualityScore` rows per strategy per rebalance cycle)
- Compute rolling trend: if quality score has declined for N consecutive cycles → emit `STRATEGY_HEALTH_DEGRADING` alert
- Track realized drawdown from live peak: if `(equity - peak) / peak < -threshold` → emit `STRATEGY_DRAWDOWN_BREACH` alert
- Surface health status in governance API (`approved_live` strategy can have health=`healthy|degrading|critical`)
- Critical health can trigger auto-demotion (see FINDING-01)

---

### FINDING-10 — Sector Concentration Limits Not Implemented
**Severity:** P2
**Affected components:** `PreTradeRiskService`, `RiskLimitConfig`, universe layer

**Description:**
Risk limits operate at the gross/net exposure level and per-symbol level. There is no sector or industry concentration limit. If all strategies overweight the technology sector, the portfolio can end up with 70%+ exposure to a single sector. GICS sector tags are presumably available via the universe layer but are not integrated into the risk management pipeline.

**Why it matters:**
Sector concentration is a primary driver of correlated drawdowns. Technology names were correlated to the 2022 rate selloff; energy names were correlated to the 2020 demand collapse. A quant portfolio with no sector limits can behave as a single-factor bet despite appearing diversified at the strategy level.

**Production consequences:**
- Concentrated sector exposure is invisible to the operator from current risk dashboards
- Risk snapshot shows gross/net/leverage but not sector breakdown
- Portfolio drawdown during a sector-specific event will appear larger than expected from strategy-level analysis

**Recommended remediation:**
Add sector concentration limits to `RiskLimitConfig`:
- `max_sector_exposure_pct: dict[str, float]` — per-GICS-sector allocation cap
- In `PreTradeRiskService`: aggregate projected sector exposure before adding the new order
- Expose sector exposure breakdown in `RiskSnapshot`
- Requires symbol → sector mapping table (integrate with universe layer)

---

### FINDING-11 — Simulation Allocation Provider Uses Same PortfolioEngine (DB Coupling)
**Severity:** P2
**Affected components:** `BacktestTradingCycleOrchestrator`, `PortfolioEngine`

**Description:**
The backtest orchestrator uses the same `PortfolioEngine` as the live trading path. `PortfolioEngine.get_allocation()` reads `CapitalAllocationPolicies` and `AllocationOverrides` from the production SOR database. This means:
1. Backtest results are sensitive to the current production configuration, not a point-in-time configuration
2. If allocation policies are changed in production during a long backtest run, the backtest will use the new policies mid-run
3. A backtest that finishes today might produce different results than the same backtest run last week

**Why it matters:**
Research integrity requires that simulation results be reproducible. If the allocation configuration for a backtest is the live production configuration rather than a pinned research configuration, results cannot be deterministically reproduced. This undermines the validity of research artifacts used for promotion criteria evaluation.

**Production consequences:**
- Promotion decisions based on backtest metrics that cannot be reproduced
- Policy changes in production implicitly change the behavior of any in-flight backtest
- Cannot compare two backtest runs if policies changed between them

**Recommended remediation:**
Introduce `SimulationAllocationProvider`:
- Accepts a static `AllocationConfig` snapshot at simulation start time
- Does not query the database during the simulation; uses only the pinned config
- `SimulationArtifactIdentity` should include an `allocation_config_hash` to track which allocation config was used
- Backtest orchestrator: serialize current allocation policies and overrides at start time, pass as `AllocationConfig` to `SimulationAllocationProvider`

---

### FINDING-12 — Drawdown-Aware Allocation Scaling Not Implemented
**Severity:** P2
**Affected components:** `QualityBasedReallocationService`, `PortfolioEngine`, allocation system

**Description:**
`max_drawdown_allowed` exists in `CapitalAllocationPolicies` and `AllocationOverrides`, but it is a threshold for blocking position sizing, not a mechanism for reducing allocation proportionally as drawdown approaches the limit. A strategy at 4% drawdown with a 5% limit receives the same allocation as one with 0% drawdown.

**Why it matters:**
Drawdown-aware allocation is a fundamental risk management technique: reduce capital exposure as a strategy approaches its risk limit, rather than running at full size until the hard limit is hit. This reduces the frequency of hard limit breaches and gives degrading strategies time to recover with smaller positions.

**Production consequences:**
- Strategy runs at full allocation until drawdown hits the hard cap
- At the hard cap, position sizing is blocked entirely (sharp position reduction, not gradual)
- Abrupt allocation cliff creates predictable trading patterns (forced selling at drawdown limit)
- No early reduction signal for operators or downstream systems

**Recommended remediation:**
Add `DrawdownScalingService` or integrate into `PositionSizer`:
- Compute `drawdown_utilization = realized_drawdown / max_drawdown_allowed`
- Apply scaling curve: linear scaling starts at 50% utilization, reaches 0 at 100%
- `drawdown_scalar = max(0, 1 - 2 * max(drawdown_utilization - 0.5, 0))`
- Multiply into position notional alongside vol_scalar and sharpe_scalar
- Expose `drawdown_utilization` in `AllocationResult` for observability

---

### FINDING-13 — Stale Total Capital Can Drive Over-Allocation
**Severity:** P2
**Affected components:** `PortfolioEngine.get_allocation()`, `StrategyAllocationService`, `CashSnapshot`

**Description:**
`PortfolioEngine` resolves `total_capital` dynamically by querying the latest `CashSnapshot.equity`. If `CashSnapshot` is stale (e.g., broker snapshot hasn't been updated, Alpaca API is down, or the last run failed before writing a snapshot), the platform allocates based on outdated equity. If equity has decreased (portfolio is down), allocations based on stale higher equity will oversize positions.

**Why it matters:**
During drawdown periods, the equity is decreasing. If snapshots fail to write (common during volatile periods when execution fails more frequently), the allocation engine may use equity from a pre-drawdown period to size positions, inflating order sizes precisely when the portfolio is most stressed.

**Production consequences:**
- During volatile periods, equity staleness causes systematic over-sizing
- Drawdown spirals: larger positions during drawdown → larger losses → equity continues to fall
- Staleness is invisible unless operators actively monitor snapshot ages

**Recommended remediation:**
- Add snapshot staleness check: `PortfolioEngine` should reject a `CashSnapshot` older than `max_snapshot_age_seconds` (configurable; e.g., 10 minutes for live trading)
- If stale: raise `StaleCapitalDataError` — halt allocation rather than use stale data
- Expose `snapshot_age_seconds` in `AllocationResult` for monitoring
- Add `CASH_SNAPSHOT_STALE` audit event when staleness threshold exceeded

---

### FINDING-14 — Auto-Rebalance Does Not Respect Override Expiration at Write Time
**Severity:** P2
**Affected components:** `QualityBasedReallocationService`, `AllocationOverrides`

**Description:**
When `QualityBasedReallocationService.rebalance()` writes new auto-generated overrides, it sets `overridden_by = "auto_rebalance"` and `expires_at = None`. Manual overrides may have an `expires_at` set by the operator. When `rebalance()` checks whether to skip a strategy (`overridden_by != "auto_rebalance"`), it will respect an expired manual override as if it were still active if the expiry check is done at policy lookup time but not at rebalance write time.

**Why it matters:**
An operator might set a temporary manual override with `expires_at = now + 24h`. After 24 hours, the override expires. The next rebalance cycle should treat that strategy as variable. However, if the expiry check is only done in `get_allocations_for_active_strategies()` and not in the rebalance service's override classification, the rebalancer might see a non-auto override and skip that strategy indefinitely.

**Production consequences:**
- Expired manual overrides ghost the rebalancer: strategy is perpetually excluded from quality-based rebalancing
- Strategy allocation remains at its last manual value long after the operator's intent has lapsed
- No mechanism alerts the operator that an expired override is still influencing behavior

**Recommended remediation:**
In `QualityBasedReallocationService._classify_strategy()`: filter overrides with `WHERE is_active = TRUE AND (expires_at IS NULL OR expires_at > NOW())` — the same condition used in `PortfolioEngine.get_allocation()`. Ensure expiry semantics are consistent across all services that read overrides.

---

### FINDING-15 — Metrics Lookup Fallback Chain Creates Promotion Ambiguity
**Severity:** P2
**Affected components:** `StrategyGovernanceService._assert_promotion_criteria_met()`, `AutoPromotionService._metrics_for_strategy()`

**Description:**
The metrics lookup chain (source_run_id → latest by strategy_id → metrics_json fallback) means the metrics used for promotion may not be the metrics the operator intended. If `source_run_id` is null on the governance record, the system uses the latest simulation run for that strategy_id. If that run is a poorly-scoped exploratory simulation from 3 days ago, not the formal evaluation run, the wrong metrics drive the promotion decision.

**Why it matters:**
The audit log records that a promotion happened and what the metrics were at evaluation time, but it doesn't record which simulation run was used or why the fallback chain resolved as it did. This makes governance audits incomplete: "why was this strategy promoted?" cannot be fully answered if the `source_run_id` is null.

**Production consequences:**
- Unintended simulations can satisfy promotion criteria if their metrics happen to exceed thresholds
- Formal evaluation runs are indistinguishable from exploratory runs in the lookup chain
- Governance audit trail is incomplete for null-source_run_id promotions

**Recommended remediation:**
- Require `source_run_id` to be non-null for promotions to `approved_paper` and `approved_live`
- Validate at `transition()` call time: raise `MissingSourceRunError` if `source_run_id` is null and target state requires it
- Record the resolved `source_run_id` (and which fallback level was used) in the promotion audit event metadata

---

### FINDING-16 — No Portfolio-Level Drawdown Governance
**Severity:** P2
**Affected components:** `QualityBasedReallocationService`, governance layer, scheduler

**Description:**
Individual strategies have `max_drawdown_allowed` at the policy level. The portfolio as a whole has no drawdown governance mechanism. There is no circuit breaker that halts all trading when the total portfolio equity curve drops by more than a defined percentage.

**Why it matters:**
In a correlated market event, multiple strategies may breach their individual limits simultaneously. The individual demotion/rebalancing responses may lag the speed of drawdown. A portfolio-level drawdown circuit breaker would halt all new positions until the portfolio recovers or an operator explicitly resumes.

**Production consequences:**
- No automatic stop on total portfolio loss; relies entirely on individual strategy limits and operator manual response
- In correlated crash scenarios, all strategies degrade simultaneously; individual limits provide less protection than a portfolio-level limit

**Recommended remediation:**
Add `portfolio_max_drawdown_pct` to operator settings:
- Monitor portfolio equity curve (from aggregated `CashSnapshot` across strategies)
- If `portfolio_drawdown > portfolio_max_drawdown_pct`: emit `PORTFOLIO_DRAWDOWN_BREACH`, pause all rebalancing, require operator confirmation to resume
- Optionally: trigger global kill switch automatically

---

### FINDING-17 — Scheduler Race Condition on Concurrent Rebalance Cycles
**Severity:** P2
**Affected components:** `run_allocation_rebalance_cycle.py`, `QualityBasedReallocationService`, `AllocationOverrides`

**Description:**
Rebalance cycles read current overrides, compute new allocations, deactivate existing overrides, and write new ones. There is no distributed lock or optimistic concurrency control guarding this sequence. If two rebalance jobs run concurrently (e.g., due to Airflow schedule overlap, manual trigger during an automated run, or slow execution), both will read the same set of current overrides, compute two sets of new allocations, and both will attempt to deactivate and rewrite overrides.

**Why it matters:**
The interleaving of two concurrent rebalance cycles is non-deterministic. One cycle may deactivate the overrides the other just wrote. The final allocation state depends on execution order, not on the intended rebalance result. The audit log will show two STRATEGY_ALLOCATION_REBALANCED events with different before/after values — no indication of a race.

**Production consequences:**
- Non-deterministic allocation state after concurrent rebalances
- Both cycles report success; neither indicates a conflict
- If one cycle's results overwrite the other's, the later-writing cycle's quality computation is based on stale before-state

**Recommended remediation:**
Add an idempotency / advisory lock to rebalance execution:
- Acquire a database advisory lock (PostgreSQL `pg_advisory_xact_lock`) at the start of `QualityBasedReallocationService.rebalance()`
- If lock is unavailable: skip this run and emit `REBALANCE_SKIPPED_CONCURRENT` audit event
- Alternatively: optimistic locking on `AllocationOverrides` with a `version` column; detect conflict and abort

---

### FINDING-18 — Volatility Scalar Integration Is Incomplete (TASK-193)
**Severity:** P2
**Affected components:** `PortfolioConstructionService`, `PositionSizer`

**Description:**
Code comments in `PortfolioConstructionService` indicate that TASK-193 left volatility scaling only partially plugged in. The combined scalar (vol × Sharpe, clamped to (0, 1]) is implemented in `PositionSizer`, but the service that computes `vol_scalar` and `sharpe_scalar` and passes them through `generate_order_intents()` may not consistently populate them. If both scalars are `None`, full notional is used — the scaling is bypassed silently.

**Why it matters:**
Volatility-aware sizing is a core risk management technique for live trading. Bypassing it silently means high-volatility periods use full position size, which is the opposite of what volatility scaling is designed to do.

**Production consequences:**
- Position sizes do not adapt to volatility regime
- High volatility periods carry full-size positions instead of reduced positions
- Risk concentrations in volatile periods are larger than risk management design intends

**Recommended remediation:**
- Complete TASK-193: ensure vol_scalar is consistently computed and passed through the pipeline
- Add observability: emit `POSITION_SCALING_ABSENT` audit event when both scalars are None and notional exceeds a threshold
- Consider making vol_scalar computation mandatory for live trading (raise if unavailable) rather than gracefully falling back to full size

---

## 3. Operational Failure Scenario Matrix

| # | Scenario | Affected Components | Failure Mode | Current Handling | Production Consequence | Gap |
|---|---|---|---|---|---|---|
| F-01 | API pod restart during active kill switch | `KillSwitchService` | Kill switch silently disabled | None | Trading resumes without operator knowledge | No persistence (FINDING-03) |
| F-02 | CashSnapshot write fails before allocation cycle | `PortfolioEngine`, `CashSnapshot` | Stale equity used for allocation | No staleness check | Oversized positions during drawdown | No staleness guard (FINDING-13) |
| F-03 | Two strategies both buy same symbol to limit | `PreTradeRiskService` | Portfolio symbol exposure 2× intended | No cross-strategy check | Concentrated drawdown on symbol shock | No portfolio-level symbol limit (FINDING-02) |
| F-04 | Concurrent rebalance jobs triggered | `QualityBasedReallocationService` | Non-deterministic allocation state | No distributed lock | Inconsistent overrides; both report success | No concurrency control (FINDING-17) |
| F-05 | All PromotionRules thresholds are null | `StrategyGovernanceService` | Any strategy passes promotion | No null guard | Under-tested strategy promoted to live | Null threshold bypass (FINDING-07) |
| F-06 | Manual overrides sum to > 100% capital | `PortfolioEngine`, `PositionSizer` | Over-allocation at 2× intended leverage | No aggregate budget check | Broker margin call or over-leveraged positions | No aggregate validation (FINDING-04) |
| F-07 | Live strategy Sharpe collapses post-promotion | `AutoPromotionService`, governance | Strategy retains `approved_live` state | Quality rebalancer reduces weight | Continued capital bleed until manual demotion | No auto-demotion (FINDING-01) |
| F-08 | Source_run_id null; wrong sim used for promotion | `StrategyGovernanceService` | Wrong metrics drive promotion decision | Fallback chain silently resolves | Governance integrity cannot be audited | Metrics ambiguity (FINDING-15) |
| F-09 | Two strategies generate opposing AAPL signals | `PortfolioConstructionService` | Wash trade: buy and sell same symbol | No conflict resolution | Double commission, misleading net exposure | No signal aggregation (FINDING-08) |
| F-10 | Drawdown approaches limit; allocation unchanged | `PositionSizer`, allocation system | Full-size positions near risk limit | Hard block at limit | Abrupt position liquidation at limit hit | No drawdown scaling (FINDING-12) |
| F-11 | Tech sector allocation reaches 80% portfolio | `PreTradeRiskService` | No sector limit enforcement | No check | Correlated sector drawdown | No sector limits (FINDING-10) |
| F-12 | Expired manual override ghosts rebalancer | `QualityBasedReallocationService` | Strategy excluded from rebalancing indefinitely | No expiry check at rebalance write | Stale allocation persists after operator intent lapses | Override expiry inconsistency (FINDING-14) |
| F-13 | Allocation policy changed during long backtest | `PortfolioEngine` (backtest) | Backtest uses mid-run policy change | No config pinning | Irreproducible research artifacts | DB coupling in simulation (FINDING-11) |
| F-14 | Portfolio equity drops 30% in single session | Governance, scheduler | No portfolio-level circuit breaker | Individual strategy limits only | All strategies continue trading | No portfolio drawdown governance (FINDING-16) |
| F-15 | Rebalance triggered every 5 minutes | `QualityBasedReallocationService` | High allocation churn, excessive turnover | No frequency guard | Transaction cost bleed, oscillating positions | No rebalance stability controls (FINDING-06) |

---

## 4. Portfolio Intelligence Maturity Assessment

### Scoring Matrix

Each dimension scored 1–5:
- **1** = Not implemented
- **2** = Minimal skeleton, non-functional for production
- **3** = Functional for basic use, meaningful gaps
- **4** = Solid implementation, minor gaps
- **5** = Institutional-grade

| Dimension | Score | Summary |
|---|---|---|
| **Governance FSM Correctness** | 4/5 | Well-structured FSM, role-based transitions, criteria gate. Missing: auto-demotion, null threshold guard |
| **Allocation Policy Architecture** | 3/5 | Policy + override merge pattern is sound. Missing: aggregate budget validation, drawdown scaling, expiry consistency |
| **Quality-Based Rebalancing** | 3/5 | Reasonable quality scoring, weighted distribution. Missing: live metrics, frequency guard, turnover model |
| **Position Sizing Intelligence** | 3/5 | Policy + vol/sharpe scalars wired. Missing: TASK-193 completion, drawdown scalar, staleness check |
| **Portfolio-Level Risk Management** | 2/5 | Pre-trade gates exist per-strategy. Missing: cross-strategy limits, sector limits, portfolio drawdown circuit breaker |
| **Multi-Strategy Coordination** | 1/5 | No signal aggregation, no position netting across strategies |
| **Strategy Health Monitoring** | 2/5 | Quality score computed per cycle. Missing: persistent health history, trend detection, degradation alerts |
| **Operational Safety** | 2/5 | Kill switch exists; allocation overrides exist. Missing: kill switch persistence, capital budget guard |
| **Research / Runtime Consistency** | 3/5 | Same portfolio engine used. Missing: allocation config pinning for simulations |
| **Observability & Explainability** | 4/5 | Before/after allocation traceability, JSONB audit events. Missing: live health dashboard, sector exposure breakdown |
| **Failure Handling & Recovery** | 2/5 | Audit events on failure. Missing: staleness guards, concurrency controls, partial-failure recovery |

**Overall Portfolio Intelligence Maturity: 2.7 / 5**

The platform has a solid governance FSM and well-structured allocation policy model. The primary gap is between single-strategy correctness (each component works for one strategy in isolation) and portfolio-level correctness (multiple strategies interacting, sharing capital, affecting shared risk limits).

---

## 5. Prioritized Remediation Roadmap

### Phase 1 — Operational Safety Fixes (P0, implement before live capital)

These gaps can cause incorrect behavior in production. They should be addressed before any live trading begins.

| Priority | Finding | Effort | Impact |
|---|---|---|---|
| 1 | Persist kill switch state to SOR (FINDING-03) | Low | Critical safety |
| 2 | Aggregate allocation budget validation (FINDING-04) | Low | Prevents over-leveraging |
| 3 | Cross-strategy symbol exposure limit (FINDING-02) | Medium | Prevents concentration |
| 4 | Auto-demotion on performance breach (FINDING-01) | High | Closes governance loop |
| 5 | PromotionRules null threshold guard (FINDING-07) | Low | Governance integrity |

### Phase 2 — Allocation Integrity Improvements (P1, short-term)

| Priority | Finding | Effort | Impact |
|---|---|---|---|
| 6 | Source_run_id required for paper/live promotion (FINDING-15) | Low | Audit integrity |
| 7 | Rebalance frequency guard + hysteresis (FINDING-06) | Low | Reduces unnecessary turnover |
| 8 | Concurrent rebalance advisory lock (FINDING-17) | Medium | Eliminates race condition |
| 9 | Override expiry consistency in rebalancer (FINDING-14) | Low | Closes expiry gap |
| 10 | Cash snapshot staleness guard in PortfolioEngine (FINDING-13) | Low | Prevents stale allocation |

### Phase 3 — Portfolio Intelligence Upgrades (P2, medium-term)

| Priority | Finding | Effort | Impact |
|---|---|---|---|
| 11 | Live performance metrics for quality scoring (FINDING-05) | High | Makes rebalancing adaptive |
| 12 | Drawdown-aware position scaling (FINDING-12) | Medium | Smoother risk reduction |
| 13 | Portfolio drawdown circuit breaker (FINDING-16) | Medium | Portfolio-level safety net |
| 14 | Multi-strategy signal aggregation / conflict resolution (FINDING-08) | High | Eliminates wash trades |
| 15 | Complete TASK-193 vol scalar integration (FINDING-18) | Medium | Volatility-aware sizing |

### Phase 4 — Institutional-Grade Enhancements (P3, long-term)

| Priority | Finding | Effort | Impact |
|---|---|---|---|
| 16 | Sector concentration limits (FINDING-10) | Medium | Correlated exposure control |
| 17 | Simulation allocation config pinning (FINDING-11) | Medium | Research reproducibility |
| 18 | Strategy health decay detection service (FINDING-09) | High | Early warning system |
| 19 | Performance decay metrics dashboard | High | Operator observability |
| 20 | Cross-strategy position netting and gross/net reconciliation | High | Portfolio construction correctness |

---

## 6. Recommendations for Institutional-Grade Portfolio Orchestration

### 6.1 Introduce a Portfolio State Bus

Currently, each strategy and service reads its own slice of state (positions, cash, risk) independently. An institutional portfolio layer requires a centralized **Portfolio State Bus** that aggregates:
- All active strategy positions (merged view)
- Aggregate symbol exposure across strategies
- Aggregate sector exposure
- Portfolio equity curve (not per-strategy)
- Live quality scores with trend

This bus becomes the single source of truth for cross-strategy risk calculations and operator dashboards.

### 6.2 Separate "Live Metrics" from "Research Metrics"

The current data model conflates research simulation metrics with live trading metrics in `MetricsSummary`. Institutional systems maintain separate metric lineages:
- **Research metrics**: from backtest/simulation, used for promotion criteria evaluation
- **Live metrics**: from realized fills, used for dynamic allocation and health monitoring
- **Blended metrics**: weighted combination, used for quality scoring with increasing weight on live as history grows

### 6.3 Implement a Strategy Health Lifecycle Separate from Governance State

Governance state (`approved_live`) reflects approval status, not real-time health. Add a parallel **strategy health dimension**:
```
HealthStatus: healthy | watch | degrading | critical | suspended
```
- `watch`: metrics declining but not breaching thresholds
- `degrading`: metrics below thresholds consistently
- `critical`: drawdown near limit or sustained losses
- `suspended`: auto-demoted pending operator review

Health status drives allocation adjustment independently of governance state. This allows adaptive capital reduction without triggering a full governance demotion (which carries role-change and audit implications).

### 6.4 Implement Allocation Versioning

Current allocation overrides are point-in-time. Institutional systems maintain an **allocation version log** with effective dates:
- Every allocation state (policy + overrides) is versioned with an effective timestamp
- Backtests and simulations reference allocation versions by effective date
- Operators can view the allocation history: "what was the allocation to Strategy A on March 15?"
- Research runs declare which allocation version they used in their artifact identity

### 6.5 Build a Portfolio Construction Layer with Explicit Netting

Replace the current per-strategy independent order generation with a two-phase pipeline:
1. **Signal Phase**: each strategy generates signals (not orders)
2. **Portfolio Phase**: `PortfolioConstructionService` aggregates signals, applies netting, concentration limits, and sector limits, then generates portfolio-level order intents

This architecture allows centralized portfolio-level constraints to be applied cleanly without each strategy needing awareness of others.

### 6.6 Drawdown Governance Ladder

Replace the binary `max_drawdown_allowed` cliff with a multi-level ladder:
```
Drawdown < 50% of limit  → Full allocation
Drawdown 50–75% of limit → 50% allocation (warning)
Drawdown 75–90% of limit → 25% allocation (probation)
Drawdown 90–100% of limit → 0% allocation (suspended)
Drawdown > limit → governance demotion triggered
```
This creates a smooth, predictable drawdown response rather than abrupt allocation removal.

### 6.7 Governance Audit Completeness

Audit events currently record what changed, but not always why in a machine-readable form. Enhance governance audit events with:
- `trigger_source`: `operator_manual | auto_promotion | auto_demotion | system_risk`
- `criteria_evaluated`: serialized list of criterion name + threshold + actual value + pass/fail
- `metrics_source_run_id`: resolved run used for criteria evaluation (never null in audit)
- `superseded_by`: for amendments, pointer to the superseding audit event

---

## Appendix: Key File Inventory

| Component | File Path |
|---|---|
| Governance service | `src/autonomous_trading_platform/application/services/strategy_governance_service.py` |
| Auto-promotion service | `src/autonomous_trading_platform/application/services/auto_promotion_service.py` |
| Allocation service | `src/autonomous_trading_platform/application/services/strategy_allocation_service.py` |
| Quality rebalancer | `src/autonomous_trading_platform/application/services/quality_based_reallocation_service.py` |
| Portfolio engine | `src/autonomous_trading_platform/portfolio/portfolio_engine.py` |
| Position sizer | `src/autonomous_trading_platform/execution/services/position_sizer.py` |
| Portfolio construction | `src/autonomous_trading_platform/execution/services/portfolio_construction_service.py` |
| Pre-trade risk | `src/autonomous_trading_platform/safety/services/pre_trade_risk_service.py` |
| Risk snapshot service | `src/autonomous_trading_platform/execution/services/risk_snapshot_service.py` |
| Kill switch | `src/autonomous_trading_platform/safety/services/kill_switch_service.py` |
| Allocation policies model | `src/autonomous_trading_platform/storage/sor/models/capital_allocation_policies.py` |
| Allocation overrides model | `src/autonomous_trading_platform/storage/sor/models/allocation_overrides.py` |
| Governance ORM model | `src/autonomous_trading_platform/storage/sor/models/strategy_governance.py` |
| Promotion rules model | `src/autonomous_trading_platform/storage/sor/models/promotion_rules.py` |
| Rebalance cycle entrypoint | `src/autonomous_trading_platform/scheduler/cycles/run_allocation_rebalance_cycle.py` |
| Backtest orchestrator | `src/autonomous_trading_platform/scheduler/backtest/backtest_trading_cycle_orchestrator.py` |
| Market trading DAG | `src/autonomous_trading_platform/scheduler/airflow/dags/market_trading_dag.py` |
| Audit log model | `src/autonomous_trading_platform/storage/sor/models/audit_logs.py` |
| Strategy routes (API) | `src/autonomous_trading_platform/interfaces/rest/routes/strategies_routes.py` |
| Governance contracts | `src/autonomous_trading_platform/contracts/governance/strategy_governance.py` |
