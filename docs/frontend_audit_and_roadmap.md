# Frontend Audit & Modernization Roadmap

**Date:** 2026-06-04
**Branch:** `cli-entrypoint-updates-platform-test-prep`
**Status:** Frontend is a static mockup — all data from `frontend/src/mock/data.ts`, zero real API calls. Backend has grown substantially beyond what the frontend was designed to show.

---

## Executive Summary

When the frontend was built, the platform backend was nascent. Since then the backend has gained: a 5-rung drawdown governance ladder, strategy health lifecycle with allocation penalties, blended metric lineage (research vs. live), factor exposure and neutralization, portfolio construction signal pipeline, shadow runtime validation, governance audit trails with amendment chains, portfolio-level drawdown governance, signal netting (4 policies), sector concentration limits, volatility scaling, and a full platform replay engine with 22 fixture configs and 18 domain hooks.

The frontend exposes none of this. Every page shows mock data. This document catalogs what exists, what the backend serves, and what the updated frontend needs.

---

## Part 1 — Current Frontend State (All Mock Data)

### Pages & Routes

| Route | Component | Status |
|-------|-----------|--------|
| `/` | `Dashboard.tsx` | Stub — portfolio KPIs, equity curve, activity feed, health aggregate |
| `/portfolio` | `Portfolio.tsx` | Stub — holdings, allocation bars, risk metrics, empty sector card |
| `/strategy` | `StrategyLab.tsx` | Stub — strategy cards, filter bar, comparison table, experiment shortcut |
| `/controls` | `Controls.tsx` | Stub — kill switch, strategy toggles, allocation overrides, governance pending, audit log |
| `/settings` | `Settings.tsx` | Stub — risk sliders, governance toggles, data config, notifications |
| `/experiments` | `ExperimentLab.tsx` | Stub — experiment cards, strategy table, create modal |

No pages exist for: Risk Detail, Operations/Jobs, Governance Audit, Platform Replay, Universe Management, Factor Analysis, Shadow Validation, Correlation Monitor.

---

### Dashboard — What Is Shown

**Mock entities used:** `mockPortfolioSummary`, `mockEquityCurve`, `mockActivity`, `mockStrategies`, `mockSystemHealth`, `mockRiskMetrics`

**Displayed:**
- 4 KPI cards: Portfolio Value + daily PnL, Total PnL ($ + %), Active Strategy count (live/paper split), Risk Status badge
- Equity curve with period buttons (1W / 1M / 3M / 1Y)
- Active strategies table: name, mode badge, Sharpe (hardcoded `"—"`), today's return, allocation, enabled toggle
- System health card: single aggregate status + trading mode string
- Risk snapshot card: drawdown, volatility, Sharpe 30d, VaR 95%
- Recent activity feed (10 items)

**Interactions:** period selector buttons only; enable toggle on strategies (state local, no mutation)

**Real gaps visible to user:**
- Sharpe column hardcoded `"—"` — looks broken
- System health is one badge, can't tell which component failed
- No governance ladder state visible anywhere on the landing page

---

### Portfolio — What Is Shown

**Mock entities:** `mockPortfolioSummary`, `mockEquityCurve`, `mockDrawdownSeries`, `mockHoldings`, `mockStrategyAllocation`, `mockSectorAllocation`, `mockRiskMetrics`

**Displayed:**
- 4 KPI cards: Total Value, Invested Capital, Cash Reserve, Open Positions
- Combined equity + drawdown chart (area + line), period selector, series toggles
- Holdings table: symbol, qty, avg price, current price, market value, unrealized PnL, strategy name
- Allocation bars by strategy (colored)
- Risk metrics: Sharpe, Sortino, Max DD, Volatility, Beta, VaR 95%
- Sector exposure card: **placeholder — renders nothing, awaiting endpoint**

**Missing entirely:** factor exposures, per-period performance table, portfolio construction output, drawdown ladder per strategy, risk budget allocation, correlation summary

---

### Strategy Lab — What Is Shown

**Mock entities:** `mockStrategies`, `mockExperiments`

**Displayed:**
- Filter bar: All / Live / Paper / Research / Off
- Strategy card grid (3 columns): name, governance state badge, type, asset class, Sharpe, CAGR, max DD, win rate, trades 30d, allocation, sparkline (**flat placeholder**), stage badge
- Action buttons per card: Detail / Pause (live), Detail / Demote (underperforming), Detail / Promote (paper), Detail / Reject (research)
- Strategy comparison table (multi-select)
- "+ New Experiment" shortcut

**Missing entirely:** health status badge (healthy/watch/degrading/critical/suspended), drawdown ladder rung per card, metric lineage indicator (Research vs. Live vs. Blended), allocation penalty indicator, real sparklines, strategy detail drawer

---

### Controls — What Is Shown

**Mock entities:** `mockStrategies`, `mockAuditLog`, `mockSystemHealth`, `mockStrategyAllocation`

**Layout:** 3-column grid

**Left:** Kill switch card (halt/resume with reason), Strategy toggles (enable/disable per strategy with reason), Environment card (mode selector, auto-promote toggle, risk checks toggle)

**Middle:** Allocation overrides (edit % per strategy with reason, "+ Add Override"), Governance pending (research strategies, Promote button)

**Right:** Audit log (7 most recent entries)

**Missing:** Pause (separate from kill switch), drawdown ladder per strategy, breach acknowledgement flow, strategy health state display, operations alerts, governance state machine flow (only a single "Promote" button exists), controls timeline history, "Clear Override" action

---

### Settings — What Is Shown

**Mock entities:** Hardcoded defaults, `GET /api/v1/settings` and metadata endpoints referenced but not wired

**Left column:**
- Risk Parameters: max portfolio drawdown slider, max strategy drawdown slider, risk tolerance slider (Low/Medium/High), max capital per strategy slider, target portfolio volatility slider
- Data & Simulation: dataset version display, feature version display, slippage model dropdown, transaction cost dropdown

**Right column:**
- Governance & Allocation: auto-promote toggle, auto-rebalance toggle, rebalance frequency, auto-demote toggle, legacy thresholds badge (Deprecated)
- Notifications: drawdown alerts toggle, strategy promotion toggle, pipeline failures toggle, kill switch toggle (always-on), daily PnL toggle (stubbed)

**Missing:** drawdown ladder threshold config, health lifecycle config (observe/alert/enforce mode, penalty scalars, cooldown windows), advanced settings (per-strategy overrides, position caps, cost model), promotion rules editor, metric lineage alpha config, universe config, settlement days, slippage calibration status

---

### Experiment Lab — What Is Shown

**Mock entities:** `mockExperiments`, `mockExperimentStrategies`

**Displayed:**
- Experiment card grid: name, status badge, type, symbols, date range, progress bar (running), strategies count, passed count, best Sharpe, best return
- Strategies panel (expanded per experiment): ID, Sharpe, Return, Max DD, Stage, Governance State, Promote button
- Filter: All / Running / Completed / Failed; sort dropdown
- Create modal: name, type, symbols, date range, price basis, strategy count, parameter ranges JSON

**Missing:** robustness scores, 6-stage validation results, ML-assisted ranking scores, regime conditioned metrics, research cache status, composite score highlighted, source_run_id for promotion flow, shadow validation linkage

---

### TypeScript Types Currently Defined

```typescript
type GovernanceState = 'proposed' | 'research' | 'paper' | 'live' | 'rejected' | 'retired'
type ExperimentType = 'backtest' | 'parameter_sweep' | 'ab_comparison' | 'rolling_window'
type ExperimentStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled'
type Environment = 'backtesting' | 'paper' | 'live'
type SimulationStage = 1 | 2 | 3 | 4

interface Strategy { id, name, type, governanceState, sharpe, cagr, maxDrawdown, winRate, trades30d, allocation, pnl30d, stage, enabled, sparkline }
interface Holding { symbol, qty, avgPrice, currentPrice, value, pnl, strategyName }
interface ActivityItem { id, text, meta, type: 'fill'|'paper'|'warning'|'system', timestamp }
interface AuditEntry { id, time, text, actor }
interface RiskMetrics { maxDrawdown, volatility, sharpe, sortino, beta, var95 }
interface SystemHealth { dataPipeline, executionEngine, featureStore, governance: 'healthy'|'delayed'|'error' }
interface PortfolioSummary { totalValue, dayPnl, dayPnlPct, totalPnl, totalPnlPct, cash, invested, openPositions, activeStrategies, liveStrategies, paperStrategies }
interface ExperimentSummary { experiment_id, experiment_name, experiment_type, status, created_at, symbols[], start_date, end_date, dataset_version, total_strategies, strategies_passed_filters, best_sharpe, best_return, progress }
interface ExperimentStrategy { strategy_id, sharpe_ratio, total_return, max_drawdown, simulation_stage, governance_state, composite_score, status }
```

---

### API Endpoints Referenced in Frontend Service Files (All Unwired)

| Path | Method | Purpose |
|------|--------|---------|
| `/api/v1/portfolio/summary` | GET | Portfolio snapshot |
| `/api/v1/portfolio/equity-curve` | GET | Equity curve chart |
| `/api/v1/portfolio/holdings` | GET | Positions table |
| `/api/v1/portfolio/allocation` | GET | Strategy allocation |
| `/api/v1/portfolio/risk` | GET | Risk metrics |
| `/api/v1/portfolio/performance` | GET | Performance stats |
| `/api/v1/strategies` | GET | Strategy list |
| `/api/v1/strategies/active` | GET | Active strategies for dashboard |
| `/api/v1/strategies/allocations` | GET | Allocation state |
| `/api/v1/strategies/{id}/enabled` | PUT | Toggle enabled |
| `/api/v1/strategies/{id}/allocation` | PUT | Override allocation |
| `/api/v1/strategies/{id}/governance/transition` | POST | Governance state change |
| `/api/v1/system/health` | GET | System health |
| `/api/v1/system/trading-mode` | PUT | Mode change |
| `/api/v1/activity/recent` | GET | Activity feed |
| `/api/v1/audit-log` | GET | Audit log |
| `/api/v1/controls/state` | GET | Controls state |
| `/api/v1/controls/kill-switch` | POST | Halt trading |
| `/api/v1/controls/resume` | POST | Resume trading |
| `/api/v1/settings` | GET/PUT | Operator settings |
| `/api/v1/metadata/dataset-versions/latest` | GET | Dataset version |
| `/api/v1/metadata/feature-dataset-versions/latest` | GET | Feature version |
| `/api/v1/experiments` | GET/POST | Experiment list/create |
| `/api/v1/experiments/{id}/strategies` | GET | Experiment results |
| `/api/v1/experiments/{id}/cancel` | POST | Cancel experiment |

---

## Part 2 — Full Backend Capabilities

### 2.1 Portfolio Domain

| Endpoint | Response Shape |
|----------|---------------|
| `GET /portfolio/summary` | current_value, cash, invested_capital, pnl, open_positions |
| `GET /portfolio/equity-curve?period=` | date, equity_curve_value, drawdown (periods: 1d/5d/1m/3m/1y/all) |
| `GET /portfolio/performance` | total_return, sharpe, sortino, max_drawdown, volatility |
| `GET /portfolio/holdings` | symbol, qty, avg_entry, current_price, market_value, unrealized_pnl, strategy_id |
| `GET /portfolio/allocation` | by_strategy[], by_asset[] |
| `GET /portfolio/risk` | volatility, beta, var_95, drawdown, avg_correlation |
| `GET /portfolio/performance/by-period` | period[], return% — daily/weekly/monthly/yearly buckets |
| `GET /portfolio/factor-exposures/current` | factor loadings, concentration_diagnostics, warnings, duration_seconds |
| `GET /portfolio/factor-exposures/history` | historical factor snapshots |
| `GET /portfolio/factor-exposures/strategies/{id}` | per-strategy factor contribution with symbol attribution |
| `GET /portfolio/factor-neutralization/config` | optimizer config, constraints |
| `GET /portfolio/factor-neutralization/current` | original vs. target weights, exposures, constraint utilization |
| `GET /portfolio/factor-neutralization/history` | neutralization run history |
| `GET /portfolio/construction/runs` | batch runs: netting policy, signal counts, conflicts |
| `GET /portfolio/construction/runs/{batch_id}/signals` | raw per-strategy signals |
| `GET /portfolio/construction/runs/{batch_id}/netted` | aggregated signals with attribution |
| `GET /portfolio/construction/runs/{batch_id}/intents` | constraint-gated order intents |
| `GET /portfolio/construction/runs/{batch_id}/conflicts` | cross-strategy conflicts |

### 2.2 Strategy Domain

| Endpoint | Response Shape |
|----------|---------------|
| `GET /strategies` | id, display_name, status, governance_state |
| `GET /strategies/active` | strategies in approved_paper/approved_live |
| `GET /strategies/allocations` | strategy_id, allocation_pct, allocated_capital |
| `GET /strategies/{id}` | full detail: config_hash, metrics, governance history |
| `GET /strategies/{id}/equity-curve` | per-strategy equity curve + drawdown |
| `PUT /strategies/{id}/allocation` | new allocation, updated_by, updated_at |
| `PUT /strategies/{id}/enabled` | strategy_id, enabled, status |
| `POST /strategies/{id}/governance/transition` | from_state, to_state, updated_at |
| `POST /strategies/compare` | metric comparison table across strategy_ids[] |
| `GET /strategies/health` | all strategies: health_status, quality_score, drawdown |
| `GET /strategies/{id}/health` | health_status, realized_drawdown |
| `GET /strategies/health/lifecycle` | all: allocation_penalty, operator_review_required |
| `GET /strategies/{id}/health/lifecycle` | allocation_scalar, suspension_info, cooldown_expires_at, consecutive_decline_count |
| `GET /strategies/{id}/health/lifecycle/transitions` | health state transition history |
| `GET /strategies/{id}/health/lifecycle/allocation-penalty` | current penalty and scalar |
| `POST /strategies/{id}/health/lifecycle/clear-suspension` | operator clears suspension |

### 2.3 Drawdown Governance Ladder

| Endpoint | Response Shape |
|----------|---------------|
| `GET /drawdown-governance` | all strategies: ladder_state, drawdown_utilization, allocation_scalar |
| `GET /drawdown-governance/pending-ack` | breached strategies awaiting operator ACK |
| `GET /drawdown-governance/config` | thresholds, scalars, cooldown_hours, hysteresis_band, breach_requires_operator_ack |
| `GET /drawdown-governance/{strategy_id}` | per-strategy: ladder_state, utilization, scalar, cooldown |
| `GET /drawdown-governance/{strategy_id}/transitions` | ladder transition history |
| `POST /drawdown-governance/{strategy_id}/acknowledge-breach` | operator ACK to unblock breached strategy |
| `POST /drawdown-governance/run` | trigger governance evaluation: strategies_evaluated, transitions |

**Ladder states:** NORMAL → WARNING → PROBATION → SUSPENDED → BREACHED
**Allocation scalars:** 1.0 → 0.5 → 0.25 → 0.0 → 0.0
**Anti-flapping:** hysteresis band + per-rung cooldown hours

### 2.4 Governance Audit

| Endpoint | Response Shape |
|----------|---------------|
| `GET /governance-audit` | decisions with criteria, metrics_lineage, state_before/after, promotion_rule_version |
| `GET /governance-audit/{id}` | single decision with shadow_validation_status |
| `GET /governance-audit/{id}/supersession-chain` | full amendment trace |
| `POST /governance-audit/{id}/supersede` | mark original as superseded |

### 2.5 Controls & Runtime

| Endpoint | Response Shape |
|----------|---------------|
| `GET /controls/state` | kill_switch_active, trading_enabled, trading_paused, trading_mode, strategies[] |
| `POST /controls/kill-switch` | halted, canceled_order_count, triggered_at |
| `POST /controls/pause` | status: "paused", trading_paused, updated_at |
| `POST /controls/resume` | status: "resumed", trading_paused, updated_at |
| `PUT /system/trading-mode` | mode, previous_mode, updated_at |

**Note:** Pause ≠ Kill Switch. Pause stops new orders; Kill Switch halts + cancels open orders. Both need separate UI representation.

### 2.6 Metric Lineage

| Endpoint | Response Shape |
|----------|---------------|
| `GET /metrics/lineage/{id}` | has_research, has_live, has_blended, alpha (live weight), blended_score |
| `GET /metrics/research/{id}` | sharpe, max_drawdown, trade_count, win_rate (from backtest) |
| `GET /metrics/live/{id}` | realized: rolling_sharpe, realized_drawdown, rolling_win_rate |
| `POST /metrics/blended/{id}` | compute blended (research + live weighted by alpha) |
| `GET /metrics/blended/{id}` | latest blended snapshot |
| `GET /metrics/blended/{id}/history` | blended score snapshots over time |

**Alpha:** confidence-adaptive weight. Increases as live history accumulates. When alpha=0 → research only. When alpha=1 → live only.

### 2.7 Shadow Validation

| Endpoint | Response Shape |
|----------|---------------|
| `POST /shadow/runs` | create shadow run (sim vs. live) |
| `GET /shadow/runs` | list by strategy/status |
| `GET /shadow/runs/{id}` | full manifest with divergence_summary |
| `POST /shadow/runs/{id}/finalize` | validation_status, total_divergences, threshold_exceedances |
| `GET /shadow/runs/{id}/divergences` | breakdown by category |
| `GET /shadow/runs/{id}/promotion-eligibility` | promotion_eligible: bool (based on divergence counts) |

### 2.8 Operational Monitoring

| Endpoint | Response Shape |
|----------|---------------|
| `GET /operations/jobs` | job_name, schedule, last_run, status |
| `GET /operations/jobs/{name}/runs` | run_id, start_time, duration, status |
| `GET /operations/runtime-state` | mode, controls, dataset references |
| `GET /operations/alerts` | alerts[], filterable by severity/category/status |
| `POST /operations/alerts/{id}/acknowledge` | acknowledged_by, acknowledged_at |
| `POST /operations/alerts/{id}/resolve` | resolved_by, resolved_at |
| `POST /operations/alerts/{id}/snooze` | snoozed_until |
| `GET /system/health` | broker, database, scheduler, features, exchanges — component health |
| `GET /system/health/detailed` | nested per-subsystem diagnostics |

### 2.9 Settings

| Endpoint | Response Shape |
|----------|---------------|
| `GET /settings` | risk_tolerance, rebalance_frequency, auto_promote/demote/rebalance, allocations |
| `PUT /settings` | updated settings with source metadata |
| `GET /settings/risk-profile` | max_drawdown targets, portfolio volatility targets |
| `GET /settings/advanced` | per_strategy_drawdown_overrides[], position_size_caps[], cost_model_params |

### 2.10 Audit, Activity, Experiments

| Endpoint | Response Shape |
|----------|---------------|
| `GET /audit-log` | events[], filterable: action_type, strategy_id, user_id, date_range, paginated |
| `GET /activity/recent` | timestamp, action_type, actor, details |
| `GET /experiments` | id, status, type, created_at, strategy_count |
| `POST /experiments` | create with name, type, symbols[], date_range, strategy_count, parameter_ranges |
| `GET /experiments/{id}` | full config + results |
| `GET /experiments/{id}/strategies` | strategies that passed filters with scores |
| `POST /experiments/{id}/cancel` | status: cancelled |

### 2.11 Metadata

| Endpoint | Response Shape |
|----------|---------------|
| `GET /metadata/dataset-versions/latest` | version_id, created_at, symbol_coverage |
| `GET /metadata/feature-dataset-versions/latest` | feature_name, created_at |

---

### 2.12 Services Built — Not Exposed via REST (CLI / Internal Only)

These services exist and are fully tested but have no REST routes:

| Service | Domain | Capabilities |
|---------|--------|-------------|
| `PlatformBacktestRunner` | Replay | Full end-to-end historical replay, 18 domain hooks, 22 fixture configs, timeline events, failure injection |
| `PortfolioSignalAggregator` | Portfolio | Multi-strategy signal netting, 4 policies |
| `PortfolioConstructionLayer` | Portfolio | 2-phase pipeline: Collect→Aggregate→Constrain→Generate |
| `MeanVarianceOptimizer` | Portfolio | Pure-numpy PGD with KKT bisection, 4 objectives |
| `RiskBudgetingService` | Portfolio | 4 allocation modes: equal capital/vol/ERC/fixed |
| `CorrelationMonitoringService` | Risk | Correlation + covariance snapshots, greedy cluster detection |
| `SectorConcentrationLimitService` | Risk | Sector exposure limits, 3-policy unknown-sector handling |
| `DrawdownScalingService` | Risk | Drawdown-aware allocation scaling |
| `VolatilityScalingService` | Risk | Volatility scalar integration |
| `PortfolioDrawdownGovernanceService` | Risk | Portfolio-level drawdown governance (4 actions, 3 recovery modes) |
| `RegimeAnalysisService` | Research | 5-dimension regime classification |
| `ResearchIntelligenceService` | Research | ML-assisted ranking, clustering, robustness estimation |
| `ValidationPipelineService` | Research | 6-stage validation: stress tests, overfitting, parameter sensitivity |
| `StrategyGenerationCache` | Research | Lineage-safe research caching |
| `AutoPromotionService` | Governance | Promotion rules with live/backtest metric blending |
| `AutoDemotionService` | Governance | Threshold-based demotion |
| `SlippageCalibrationService` | Execution | Fill quality aggregation, calibration snapshots |

---

## Part 3 — Gap Analysis

### Gaps: Data That Exists But Frontend Never Requests

| Data | Impact | Backend Source |
|------|--------|----------------|
| Sharpe ratio on active strategies | "—" displayed on Dashboard — looks broken | Add to `ActiveStrategyResponse` from latest blended metrics |
| 30d PnL on active strategies | Shows today's return instead | Add to `ActiveStrategyResponse` |
| Per-component system health | Can't diagnose which subsystem is degraded | `GET /system/health` (detailed breakdown already in response) |
| Drawdown ladder state per strategy | Operator can't see governance ladder anywhere | `GET /drawdown-governance` |
| Strategy health status | Healthy / Watch / Degrading / Critical / Suspended — never shown | `GET /strategies/health` |
| Allocation penalty per strategy | Operator doesn't know a strategy is running at 50% allocation | `GET /strategies/{id}/health/lifecycle/allocation-penalty` |
| Pause vs Kill Switch distinction | Only Kill Switch shown; Pause exists but UI doesn't have it | `POST /controls/pause` |
| Breach acknowledgement flow | Breached strategies can stay blocked indefinitely | `GET /drawdown-governance/pending-ack` + `POST .../acknowledge-breach` |
| Operational alerts | Alerts accumulate with no UI to resolve them | `GET /operations/alerts` + action endpoints |
| Metric lineage (Research vs. Live vs. Blended) | Can't tell which metrics drive decisions | `GET /metrics/lineage/{id}` |
| Per-strategy equity curve | Sparklines are flat placeholder lines | `GET /strategies/{id}/equity-curve` |
| Sector exposure | Empty card renders on Portfolio | Endpoint does not yet exist (see §4) |
| Factor exposures | Major risk system — zero UI | `GET /portfolio/factor-exposures/current` |
| Per-period performance table | Only all-time metrics shown | `GET /portfolio/performance/by-period` |
| Portfolio construction output | What signals, netting, constraints produced the portfolio | `GET /portfolio/construction/runs` |
| Governance audit trail | Only runtime audit log shown; governance decisions not surfaced | `GET /governance-audit` |
| Shadow validation status | Promotion eligibility gated on it — but never shown | `GET /shadow/runs/{id}/promotion-eligibility` |
| Operations jobs & runtime state | No view of scheduled jobs, last run, errors | `GET /operations/jobs` |
| Settings: advanced & risk-profile | Per-strategy overrides, position caps, cost model — not configurable via UI | `GET /settings/advanced` |
| Drawdown ladder config | Thresholds/scalars/hysteresis configurable but not shown | `GET /drawdown-governance/config` |

### Gaps: Functionality That Doesn't Exist Anywhere (Backend Needed)

| Feature | What To Build |
|---------|--------------|
| Sector exposure endpoint | `GET /portfolio/sector-exposure` reading `SectorExposureReader` |
| Kill switch release endpoint | `POST /controls/kill-switch/release` — distinct from resume-from-pause |
| Correlation REST endpoint | `GET /portfolio/correlation/current` exposing `CorrelationMonitoringService` snapshots |
| Risk budget REST endpoint | `GET /portfolio/risk-budget/current` exposing `RiskBudgetingService` output |
| Platform replay REST endpoints | `POST /replay/run`, `GET /replay/runs`, `GET /replay/runs/{id}` |
| Universe management REST | `GET /universe/current`, `GET /universe/history` |

---

## Part 4 — Updated Frontend Design

### Page Changes Required

#### Dashboard — Incremental Updates

1. **Fix Sharpe column** — backend to add `sharpe_ratio_30d` + `pnl_30d_pct` + `health_state` + `drawdown_ladder_rung` + `realized_drawdown_pct` to `ActiveStrategyResponse`. Frontend renders them.
2. **Expand system health card** — use `GET /system/health` full response to show: Data Pipeline, Execution Engine, Feature Store, Governance, Broker — each with status dot and timestamp.
3. **Add Governance Ladder mini-widget** — pill counts per rung: NORMAL N / WARNING N / PROBATION N / SUSPENDED N / BREACHED N. Links to Controls.
4. **Add strategy health summary** — Healthy N / Watch N / Degrading N / Critical N / Suspended N. Links to Strategy Lab.
5. **Add unresolved alerts banner** — if `GET /operations/alerts` returns active critical/high alerts, show dismissible banner with count and link to Operations page.
6. **Wire equity curve** — replace mock with `GET /portfolio/equity-curve?period=`.
7. **Wire activity feed** — replace mock with `GET /activity/recent?limit=10`.

---

#### Portfolio — Incremental + New Sections

1. **Wire all existing cards** — summary, equity curve, holdings, allocation, risk, performance to real endpoints.
2. **Sector exposure card** — implement `GET /portfolio/sector-exposure` endpoint first, then wire the existing placeholder card. Show warning indicator per sector when approaching limit.
3. **Factor exposures section** (new, collapsible) — from `GET /portfolio/factor-exposures/current`. Table of factor loadings (market, sector, size, momentum, volatility), warning flags, top contributors. Historical chart toggle.
4. **Per-period performance table** (new) — Day / Week / Month / YTD / 1Y / All — from `GET /portfolio/performance/by-period`.
5. **Drawdown ladder strip** — per-strategy row showing current rung + utilization % + scalar. Compact, below allocation bars.
6. **Portfolio construction summary** (new, collapsible) — latest batch: netting policy, signals generated, conflicts detected, constraints applied.

---

#### Strategy Lab — Enhanced Cards + Detail Drawer

1. **Health status badge** — per card, prominent. Color-coded: green (healthy), yellow (watch), orange (degrading), red (critical), gray (suspended). From `GET /strategies/health`.
2. **Drawdown ladder badge** — per card. NORMAL (no badge), WARNING (yellow), PROBATION (orange), SUSPENDED (gray), BREACHED (red).
3. **Metric lineage indicator** — per card. Small tag: [Research] or [Live] or [Blended 0.34α]. From `GET /metrics/lineage/{id}`.
4. **Allocation penalty badge** — when penalty > 0, show: "-50% allocation" in red. From lifecycle endpoint.
5. **Real sparklines** — wire to `GET /strategies/{id}/equity-curve`.
6. **Strategy detail drawer** (new) — slide-in panel when "Detail" clicked:
   - Full metrics (research vs. live vs. blended, labeled)
   - Equity curve chart
   - Health lifecycle timeline
   - Drawdown ladder rung history
   - Shadow validation status (eligible/not eligible + divergence count)
   - Quick actions: Promote, Demote, Pause, Clear Suspension
7. **Wire comparison** — `POST /strategies/compare` for multi-select comparison table.

---

#### Controls — Significant Restructure

Reorganize from current 3-column grid into 4 logical sections:

**Section 1: System State**
- Kill switch card (existing) — split into two buttons when kill_switch_active=false: [⬛ KILL ALL] and [⏸ PAUSE]. When kill_switch_active=true: [▶ RELEASE KILL SWITCH]. When trading_paused=true (not kill switched): [▶ RESUME].
- Operations alerts card (new) — table of unresolved alerts, severity badges, Acknowledge / Snooze / Resolve inline actions. From `GET /operations/alerts`.
- Controls timeline (new, toggle) — last 20 control-class audit events as a compact timeline.

**Section 2: Strategy Controls (enhanced)**
- Strategy rows — add health state badge + drawdown ladder rung + allocation penalty indicator inline with each row.
- Governance state column — show current governance state and legal next states as transition buttons (not just "Promote").
- Actions: Enable/Disable (existing), Clear Suspension (new, shown when SUSPENDED), Allocation Override (existing + explicit Clear button).

**Section 3: Governance Lifecycle (new)**
- State machine funnel view: pending_review → approved_paper → paper_active → approved_live → live_active, with counts per stage. Click to expand strategies in each stage.
- Drawdown ladder table: all strategies with current rung, realized drawdown %, threshold %, scalar, operator ACK required flag + ACK button.
- Pending breach acknowledgements (priority panel): strategies in BREACHED state waiting for operator ACK.

**Section 4: Audit Trails (enhanced)**
- Runtime audit log tab (existing).
- Governance audit tab (new) — from `GET /governance-audit`, filterable by strategy/event type/date range.

---

#### Settings — Existing + New Sections

**Existing sections — fix and wire:**
1. Risk sliders — wire to `GET /settings` and `PUT /settings`. Add: max_portfolio_symbol_exposure_usd, max_portfolio_symbol_pct.
2. Governance toggles — add "(stored only — manual trigger required)" note next to auto-promote/rebalance/demote until scheduler wiring is complete.
3. Data & Simulation — add: settlement_days field, adverse_slippage_threshold_bps, slippage calibration last-run timestamp.

**New sections:**
4. **Drawdown Ladder Config** — configure: WARNING threshold %, PROBATION threshold %, SUSPENSION threshold %, BREACH threshold %, allocation scalars, cooldown hours per rung, hysteresis band, breach_requires_operator_ack toggle. From `GET /drawdown-governance/config`.
5. **Health Lifecycle Config** — lifecycle mode (Observe / Alert / Enforce), allocation penalty scalars (watch / degrading / critical / suspended), cooldown windows. From `GET /settings/advanced`.
6. **Advanced Risk** — per-strategy drawdown override table (editable), position size caps table. From `GET /settings/advanced`.
7. **Promotion Rules** — min_sharpe, max_drawdown, min_days_tested, min_trade_count, min_cagr, min_win_rate — editable.

---

#### Experiment Lab → Research Lab — Rename + Enhance

1. **Rename** to "Research Lab", update route to `/research`.
2. **Strategy table columns** — add: Composite Score, Robustness Score (when available), Validation Stage (N/6 passed), Metric Lineage source.
3. **Expand per-strategy row** — 6-stage validation results: stress test pass/fail, overfitting score, parameter sensitivity rating.
4. **Regime tab per experiment** — regime conditioned metrics (best Sharpe per regime type).
5. **Research cache badge** — "Cached" vs "Fresh Run" per experiment.
6. **Promote flow** — updated modal: require source_run_id selection for capital promotions, show shadow validation eligibility status before promotion.
7. **Wire all actions** — create, cancel, filter, sort — currently local state only.

---

### New Pages Required

#### `/replay` — Platform Replay

**Purpose:** Configure and run historical platform replays through the UI. Exposes the `PlatformBacktestRunner` engine.

**Layout:**
```
[Configuration Panel]                  [Recent Runs Table]
  Symbols (multi-select or text)         Run ID | Status | Ticks OK/Failed | Events | Errors
  Date range (start / end)               [Inspect] [Download Artifact]
  Starting cash (input)
  Random seed (input)

[Job Schedule Grid]
  Toggle: ingestion / corp_actions / features / trading_cycle / risk
          governance / universe / research / portfolio_snapshot / operations
  Cadence per job: daily / weekly / monthly

[Timeline Events]
  + Add Event → modal: date | type | params (dynamic per type)
  Events list sorted by date, with Remove buttons

[Failure Injections]
  Enable failure injection checkbox
  + Add Injection → modal: date | target domain | failure kind
  Injections list

[Actions]
  [Plan Run] → validates config, shows tick count estimate and event fire counts
  [Dry Run]  → executes without mutation side effects
  [Run]      → submits, shows live progress ticker
```

**Artifact Viewer (per run, when Inspect clicked):**
- Summary: total ticks, ok/failed, final equity, total return, max drawdown, timeline events applied, injections applied
- Domain timeline: per-domain status per tick (sparkline grid)
- Error log: timestamp, domain, message, severity

**New REST endpoints needed:**
```
POST /api/v1/replay/run          body: { config, dry_run, inject_failures }
GET  /api/v1/replay/runs         response: [ ReplayRunSummary ]
GET  /api/v1/replay/runs/{id}    response: PlatformBacktestArtifact (full bundle)
GET  /api/v1/replay/runs/{id}/report  response: compact summary metrics
```

---

#### `/operations` — Operations Monitor

**Purpose:** Jobs, alerts, runtime state, shadow validation, metric lineage.

**Tab 1: Jobs & Runtime**
- Runtime state card: mode, kill_switch, trading_paused, active datasets
- Scheduled jobs table: name, schedule, last_run_at, status, duration_ms, error (from `GET /operations/jobs`)
- Per-job history modal on click

**Tab 2: Alerts**
- Severity summary chips: N critical / N high / N medium
- Alert table: severity badge, category, message, created_at, status, Acknowledge / Resolve / Snooze buttons

**Tab 3: Shadow Validation**
- Per-strategy shadow run list
- Divergence breakdown by category
- Promotion eligibility badge

**Tab 4: Metric Lineage**
- Blended alpha weight (current)
- Per-strategy lineage type (Research / Live / Blended)
- Days live tracked per strategy

---

#### `/risk` — Risk Dashboard

**Purpose:** Centralized risk monitoring beyond the summary card.

**Sections:**

**Drawdown Governance Ladder:**
- Full ladder table: strategy, rung (colored badge), realized drawdown %, threshold %, allocation scalar, operator ACK status
- Rung distribution chart (count per rung)
- Pending ACK list (if any)

**Factor Exposures:**
- Current portfolio factor loading table (market, sector, size, momentum, volatility)
- Historical factor exposure trend chart (multi-line)
- Per-strategy factor decomposition (expandable rows)

**Correlation Matrix:**
- Strategy-to-strategy correlation heatmap
- Detected clusters (highlighted groups)
- Historical correlation trend

**Risk Budget:**
- Current allocation mode: equal_capital / equal_vol / ERC / fixed
- Capital allocation by risk budget (donut or bars)

**Concentration Limits:**
- Sector exposure table: sector, current %, limit %, status (ok / warning / breach)
- Symbol exposure: top 10 symbols by portfolio weight vs limit

---

#### `/governance` — Governance Detail

**Purpose:** Full governance lifecycle management — state machine, audit trail, health lifecycle.

**State Machine Funnel:**
- Visual funnel or kanban: pending_review → approved_paper → paper_active → approved_live → live_active
- Count of strategies per stage
- Click stage → list of strategies in that stage

**Per-Strategy Governance Panel (drawer):**
- Current state + legal next-state transition buttons (with reason modal)
- Transition history timeline
- Source run ID for last capital promotion
- Health state: Observe / Alert / Enforce with history
- Drawdown ladder rung + history
- Promotion eligibility score with lineage breakdown
- Shadow validation eligibility

**Governance Audit Trail:**
- Full `GET /governance-audit` feed
- Filter: strategy, event type (promotion/demotion/suspend/auto-demote), date range
- Per-entry: expand to see criteria_evaluated, metrics_lineage, state_before/after, promotion_rule_version
- Supersede action per entry
- Amendment chain traversal for superseded entries

---

## Part 5 — New TypeScript Interfaces Needed

When wiring begins, add these to `frontend/src/types/index.ts`:

```typescript
type HealthStatus = 'healthy' | 'watch' | 'degrading' | 'critical' | 'suspended'
type LadderRung = 'normal' | 'warning' | 'probation' | 'suspended' | 'breached'
type MetricLineageType = 'research_only' | 'live_only' | 'blended' | 'none'
type AlertSeverity = 'low' | 'medium' | 'high' | 'critical'
type AlertStatus = 'active' | 'acknowledged' | 'resolved' | 'snoozed'

interface StrategyHealth {
  health_status: HealthStatus
  quality_score: number
  realized_drawdown: number
  allocation_scalar: number
  allocation_penalty: number
  operator_review_required: boolean
  consecutive_decline_count: number
  suspension_info?: { reason: string; suspended_at: string; cooldown_expires_at: string }
}

interface DrawdownLadderState {
  ladder_state: LadderRung
  drawdown_utilization: number
  allocation_scalar: number
  cooldown_expires_at?: string
  operator_ack_required: boolean
  last_transition_at: string
}

interface MetricLineage {
  lineage_type: MetricLineageType
  alpha: number
  blended_score: number
  research_sharpe?: number
  live_sharpe?: number
  days_live: number
}

interface FactorExposure {
  factor: 'market' | 'sector' | 'size' | 'momentum' | 'volatility'
  exposure: number
  warning: boolean
}

interface OperationalAlert {
  alert_id: string
  alert_name: string
  category: string
  severity: AlertSeverity
  status: AlertStatus
  details: Record<string, unknown>
  created_at: string
  acknowledged_at?: string
  snoozed_until?: string
}

interface GovernanceAuditEvent {
  governance_audit_id: string
  strategy_id: string
  event_type: string
  decision_outcome: string
  criteria_evaluated: Array<{ name: string; value: number; threshold: number; passed: boolean }>
  state_before: string
  state_after: string
  promotion_rule_version: string
  shadow_validation_status?: string
  superseded_by?: string
  created_at: string
}

interface ShadowValidationSummary {
  shadow_run_id: string
  strategy_id: string
  validation_status: 'pending' | 'passed' | 'failed'
  total_divergences: number
  threshold_exceedances: number
  promotion_eligible: boolean
  divergences_by_category: Record<string, number>
}

interface ConstructionRunSummary {
  batch_id: string
  netting_policy: string
  raw_signal_count: number
  netted_signal_count: number
  conflict_count: number
  constraint_status: 'satisfied' | 'violated' | 'partial'
  created_at: string
}

interface ReplayRunSummary {
  replay_id: string
  status: 'running' | 'completed' | 'completed_with_errors' | 'failed'
  symbols: string[]
  start_date: string
  end_date: string
  started_at: string
  completed_at?: string
  ticks_ok: number
  ticks_failed: number
  timeline_events_applied: number
  failure_injections_applied: number
  error_count: number
}
```

---

## Part 6 — Backend Contract Changes Needed

### 6.1 `ActiveStrategyResponse` — Add Fields

Required to fix the Dashboard broken Sharpe column and add health visibility:

```python
class ActiveStrategyResponse(BaseModel):
    strategy_id: str
    strategy_name: str
    governance_state: str
    trading_mode: str
    allocation_pct: Decimal
    todays_return: Decimal | None
    enabled: bool
    # NEW
    sharpe_ratio_30d: float | None      # from latest BlendedMetricsSummary
    pnl_30d_pct: float | None           # 30-day PnL percentage
    health_state: str | None            # HEALTHY/WATCH/DEGRADING/CRITICAL/SUSPENDED
    drawdown_ladder_rung: str | None    # normal/warning/probation/suspended/breached
    realized_drawdown_pct: float | None
```

### 6.2 `GET /portfolio/sector-exposure` — New Endpoint

```python
class SectorExposureItem(BaseModel):
    sector: str
    exposure_pct: float
    exposure_usd: Decimal
    limit_pct: float | None
    status: Literal["ok", "warning", "breach"]

class SectorExposureResponse(BaseModel):
    as_of: datetime
    items: list[SectorExposureItem]
    unknown_sector_pct: float
    unknown_sector_policy: str
```

### 6.3 `POST /controls/kill-switch/release` — New Endpoint

Needed to clearly distinguish "release kill switch" from "resume from pause":

```python
class KillSwitchReleaseRequest(BaseModel):
    reason: str

class KillSwitchReleaseResponse(BaseModel):
    released: bool
    kill_switch_active: bool  # False on success
    released_at: datetime
    actor: str
```

### 6.4 `GET /portfolio/correlation/current` — New Endpoint (Phase 3)

Expose `CorrelationMonitoringService` for the correlation heatmap:

```python
class CorrelationCell(BaseModel):
    strategy_a: str
    strategy_b: str
    correlation: float
    in_cluster: bool

class CorrelationSnapshotResponse(BaseModel):
    as_of: datetime
    cells: list[CorrelationCell]
    clusters: list[list[str]]
    lookback_days: int
```

### 6.5 `POST /replay/run` — New Endpoint (Phase 3)

```python
class ReplayRunRequest(BaseModel):
    symbols: list[str]
    start_date: date
    end_date: date
    starting_cash: Decimal
    seed: int
    jobs: dict[str, str]         # job_name → cadence (daily/weekly/monthly)
    timeline_events: list[dict]  # [{date, type, params}]
    failure_injections: list[dict]
    dry_run: bool = False

class ReplayRunResponse(BaseModel):
    replay_id: str
    status: str
    dry_run: bool
    artifact_path: str | None
```

---

## Part 7 — Implementation Priority

### Phase 1 — Wire & Fix (No New Pages, Minimal New Backend)

**Backend changes:** Add fields to `ActiveStrategyResponse`. Add `GET /portfolio/sector-exposure`. Add `POST /controls/kill-switch/release`.

**Frontend changes:**
1. Wire Dashboard — equity curve, portfolio summary, activity feed, system health (detailed), active strategies with new fields
2. Wire Portfolio — holdings, allocation, risk, performance, per-period table, sector exposure
3. Wire Controls — controls state, kill switch (with release), pause/resume, strategy toggles, allocation overrides, audit log
4. Wire Settings — GET/PUT settings, metadata versions
5. Wire Research Lab — experiments list, strategies list, create, cancel
6. Add drawdown ladder mini-widget to Dashboard
7. Add health summary to Dashboard
8. Add unresolved alerts banner to Dashboard
9. Add health + ladder badges to Strategy Lab cards
10. Add metric lineage indicator to Strategy Lab cards
11. Wire sparklines to per-strategy equity curve

**Estimated scope:** ~3-4 weeks

---

### Phase 2 — Enhanced UI (Existing Endpoints, New UI Components)

**Frontend changes:**
1. Governance section in Controls (state machine funnel, breach ACK flow)
2. Operational alerts card in Controls
3. Governance audit tab in Controls
4. Strategy detail drawer in Strategy Lab
5. Factor exposure section in Portfolio
6. Portfolio construction summary in Portfolio
7. Drawdown ladder table in Portfolio
8. Expanded Settings: drawdown config, health lifecycle config, advanced risk, promotion rules
9. Research Lab: validation results columns, regime tab, research cache badge, updated promote flow

**Estimated scope:** ~3-4 weeks

---

### Phase 3 — New Pages (Backend + Frontend)

**Backend changes:** Correlation endpoint, risk budget endpoint, platform replay endpoints, universe REST endpoints.

**Frontend changes:**
1. `/operations` page — jobs, alerts, shadow validation, metric lineage
2. `/risk` page — drawdown ladder, factor exposures, correlation heatmap, concentration limits
3. `/governance` page — state machine view, full governance audit trail
4. `/replay` page — replay configuration, run management, artifact viewer

**Estimated scope:** ~6-8 weeks

---

### Phase 4 — Automation Wiring (Backend Scheduler)

1. Wire `AutoPromotionService` to governance cadence scheduler (daily run)
2. Wire `AutoDemotionService` to governance cadence scheduler
3. Wire `QualityBasedReallocationService` when `auto_rebalance_enabled=true`
4. Update Settings governance toggles to reflect "active" vs "stored-only" status

---

## Part 8 — What To Keep As-Is

- **5-page nav structure** — well-suited to the domain, extend rather than replace
- **Dark terminal design tokens** — IBM Plex Mono, CSS variable palette matches quant audience
- **TanStack Router + Query** — 30s stale time is appropriate for trading data
- **Mock data in `data.ts`** — correctly isolated, extend with new entity shapes
- **Reason-required pattern** — all mutations require rationale text, keep and extend
- **Confirmation modal pattern** — for destructive actions, keep
- **shadcn/ui primitives in `components/ui/`** — do not modify
