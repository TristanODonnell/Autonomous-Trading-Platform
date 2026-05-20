# Execution & Simulation Architecture Audit

## 1. Execution Architecture Inventory

### 1.1 Live/Paper Execution Path

| Component | File | Role |
|-----------|------|------|
| `PortfolioConstructionService` | `execution/portfolio_construction_service.py` | Signal → OrderIntent, size scaling, pre-trade risk |
| `ExecutionPolicyEngine` | `execution/execution_policy_engine.py` | PASSTHROUGH / MARKET / LIMIT / TWAP / VWAP_LITE transformation |
| `OrderExecutionService` | `execution/order_execution_service.py` | Broker submission, exponential backoff retry |
| `AlpacaBrokerAdapter` | `execution/broker_adaptor.py` | OrderIntent → Alpaca REST payload |
| `AlpacaBrokerClient` | `execution/alpaca_broker_client.py` | HTTP client (paper + live base URLs) |
| `OrderStateMachineService` | `execution/order_state_machine_service.py` | State transitions, audit log |
| `OrderReconciliationService` | `execution/order_reconciliation_service.py` | Single-order broker reconciliation |
| `ExternalBrokerReconciliationService` | `execution/external_broker_reconciliation_service.py` | Full book reconciliation (orders/fills/positions/cash/equity) |
| `BrokerOrderMapper` | `execution/broker_order_mapper.py` | Broker response → internal contracts + incremental fill extraction |
| `CashLedgerService` | `execution/cash_ledger_service.py` | Fill → cash accounting |
| `PositionLedgerService` | `execution/position_ledger_service.py` | Fill → position accounting, avg cost, realized PnL |
| `RealisedSlippageService` | `execution/realised_slippage_service.py` | Two-phase fill quality measurement |
| `PaperRuntimeOrderSafeguardService` | `execution/paper_runtime_order_safeguard_service.py` | Fail-closed caps for paper mode |
| `LiveTradingGateService` | `safety/live_trading_gate_service.py` | Multi-gate live trading guard |

### 1.2 Simulation/Backtest Execution Path

| Component | File | Role |
|-----------|------|------|
| `SimulationRunner` | `research/simulation/simulation_runner.py` | End-to-end backtest orchestrator |
| `SimulationExecutionEngine` | `research/simulation/simulation_execution_engine.py` | Bar-by-bar loop, signal eval, fill application |
| `SimulatedExecutionService` | `research/simulation/simulated_execution_service.py` | Order fill simulation (MARKET + LIMIT only) |
| `OrderSimulatorService` | `research/simulation/order_simulator_service.py` | OrderIntent generation for simulation |
| `SimulationCostModelService` | `research/simulation/simulation_cost_model_service.py` | Fill price + cost computation |
| `SlippageModel` (research) | `research/simulation/slippage_model.py` | Fixed-rate slippage |
| `VolumeShareSlippageModelService` | `backtesting/volume_share_slippage_model_service.py` | Volume-share market impact (unused in simulation path) |
| `LinearCostModelService` | `backtesting/linear_cost_model_service.py` | Commission + spread + extra slippage (unused in simulation path) |

**Three execution paths exist, sharing ledger services but nothing else:**

```
[Signal] → PortfolioConstructionService → ExecutionPolicyEngine → OrderExecutionService → AlpacaBrokerClient
                                                                                              ↓ (paper API or live API)
                                                                                         [Fill via reconciliation]

[Signal] → OrderSimulatorService → SimulatedExecutionService → SimulationCostModelService
                                          ↓
                                    [Fill immediately, same bar]

Both paths feed into: CashLedgerService + PositionLedgerService (shared)
```

---

## 2. Realism Gap Analysis

### Finding R-01 — CURRENT_CLOSE Fill Policy Creates Look-Ahead Bias

**Severity: Critical**
**Affected components:** `SimulatedFillModelConfig`, `SimulatedExecutionService`, `SimulationExecutionEngine`

Signals are generated from completed bar data (bar close). Fills are then executed at `bar.close` — the same price used to generate the signal. This means the simulation executes at the exact price the strategy observed to generate the signal. In live execution the signal is generated after bar close, routed through execution policy, queued at the broker, and fills at some future price (next open, intraday, or later). The simulation erases this delay entirely.

**Production consequence:** Strategies that look marginally profitable in backtest will underperform live because every trade starts with a P&L disadvantage equal to close-to-open gap plus actual execution costs. Reverting strategies are particularly susceptible — fill at the very price driving the reversal signal is an artifact, not a realism.

**Remediation:** Implement and enforce `NEXT_OPEN` fill policy for market orders as the default for daily bar backtests. For intraday bars the correct policy is next-bar-open. The `NEXT_OPEN` enum variant already exists but is marked `# not implemented; defer`. This is the highest-priority fill model fix.

---

### Finding R-02 — Fixed Slippage Rate Ignores All Market Structure

**Severity: High**
**Affected components:** `SlippageModel` (research), `SimulationCostModelService`

The default slippage rate is 1 bps, applied symmetrically to all symbols, all sizes, all market conditions. This is a single scalar with no dependency on:
- ADV (average daily volume)
- Order size relative to ADV
- Intraday liquidity profile (open/close more liquid than midday)
- Bid-ask spread (varies by security and market regime)
- Volatility (wide spreads during earnings/announcements)
- Market cap tier (micro-cap vs large-cap liquidity is orders of magnitude different)

1 bps is also significantly below empirical retail execution costs. Institutional TWAP on liquid large-caps achieves 3–8 bps; retail market orders in mid/small-cap names routinely incur 15–50 bps all-in.

**Production consequence:** Simulation overstates strategy returns for anything except the most liquid large-cap names. Strategies tuned against 1 bps slippage will produce live returns materially worse than backtest, potentially turning edge-negative strategies. The gap is largest for turnover-intensive strategies.

**Remediation:** Wire the existing `VolumeShareSlippageModelService` (which already implements `impact_coefficient_bps * volume_share`) into the simulation path. Add per-symbol ADV lookup from the stored bar data. Calibrate `impact_coefficient_bps` from `RealisedSlippageService` live data rather than a hardcoded constant. Minimum viable improvement: use spread-aware slippage with a half-spread estimated from OHLC bar data (`(high - low) / close / 2` as a proxy).

---

### Finding R-03 — Two Parallel, Disconnected Slippage Model Systems

**Severity: High**
**Affected components:** `research/simulation/slippage_model.py`, `backtesting/volume_share_slippage_model_service.py`, `backtesting/linear_cost_model_service.py`

There are two cost model families:

- **Path A (active):** `research/simulation/` — `SlippageModel` (fixed rate) + `SimulationCostModelService`
- **Path B (inactive):** `backtesting/` — `VolumeShareSlippageModelService` + `LinearCostModelService`

Path B is the more realistic model and appears to have been designed for this purpose, but the `SimulationRunner` and `SimulationExecutionEngine` use Path A. Path B is never called during simulation. The `backtesting/` directory is orphaned from the actual simulation execution path.

**Production consequence:** The better slippage model already written is never used. Any calibration work applied to `backtesting/` slippage configs has no effect on actual simulation results. This creates a silent model error — operators may believe the volume-share model is active when it is not.

**Remediation:** Consolidate to a single cost model hierarchy. Make `SimulationCostModelService` configurable with pluggable `ISlippageModel` and `ICostModel` interfaces. Wire `VolumeShareSlippageModelService` as the production default, retiring the fixed-rate model to a testing convenience.

---

### Finding R-04 — No ADV/Liquidity Cap in Simulation Fills

**Severity: High**
**Affected components:** `SimulatedExecutionService`, `SimulationCostModelService`

Simulation fills every order at full requested quantity regardless of the bar's volume. A strategy requesting 50,000 shares of a stock that traded 10,000 shares that day will fill completely in simulation. In live execution this order would represent 5× the day's volume, be physically impossible to fill, and would move the price substantially.

The `VolumeShareSlippageModelService` has a `max_volume_share` of 10% but (a) it is not used in simulation, and (b) even if it were, it only affects slippage cost, not whether the fill can happen or how much quantity is actually filled.

**Production consequence:** Size-sensitive strategies appear viable in simulation but face fill rate problems live. The simulation overstates capacity, allowing strategies to appear scalable when they are not. Portfolio construction at live scale would face chronic partial fills and adverse market impact.

**Remediation:** Add a `max_volume_participation` constraint in `SimulatedExecutionService`. When `requested_qty > bar.volume * max_participation_rate`, cap the filled quantity and carry forward the remainder to subsequent bars. Default participation rate: 10%–20% of bar volume. This is the fill model's most important capacity-realism feature.

---

### Finding R-05 — Zero Latency Simulation

**Severity: Medium**
**Affected components:** `SimulationExecutionEngine`, `SimulatedExecutionService`

Signal generation and fill execution happen within the same iteration of the bar loop. There is no simulated order routing latency. In live execution, the signal-to-fill path involves: strategy evaluation → portfolio construction → execution policy (quote fetch) → HTTP to broker → broker queue → fill notification → reconciliation. This is typically 50–500ms for a retail broker in normal conditions.

For daily bar strategies this has minimal impact since the signal-to-next-open window is hours. For intraday bar strategies (e.g. 5-minute bars used in warmup computation), zero-latency simulation means fills occur at prices that would not be achievable in practice.

**Production consequence:** Intraday strategies are more affected than daily strategies. Opening-bar strategies (signals on first bar, fills at next bar open) are misrepresented because the simulation fills them at the signal bar's close, skipping overnight/pre-market price movement.

**Remediation:** Expose a `latency_bars` parameter in `SimulatedFillModelConfig`. A value of `1` means orders generated at bar N fill at bar N+1 open. This resolves look-ahead bias (Finding R-01) simultaneously if implemented as next-bar-open fill.

---

### Finding R-06 — Warmup Bar Count Is Hardcoded to 5-Minute Bar Assumption

**Severity: Medium**
**Affected components:** `SimulationRunner`, `SimulationExecutionEngine`

Warmup bar count is computed as `strategy.longest_lookback_days * 78`, where 78 is the number of 5-minute bars in a standard US equity trading day (9:30–16:00). This is hardcoded and assumes intraday 5-minute data.

For daily bar backtests, this yields a warmup of `lookback_days * 78` daily bars — 78× more data than needed — wasting data and distorting the effective in-sample window. For 1-minute bars, the warmup is 5× too short (390 bars/day for 1-minute vs 78 for 5-minute).

**Production consequence:** Daily bar backtests silently consume excess historical data for warmup, reducing usable in-sample period. For strategies with 50-day lookbacks, the simulation consumes an extra 3,900 daily bars (~15.5 years) purely for warmup. 1-minute bar strategies have under-seeded indicators, causing the first several hundred bars of the live window to have incorrect signal values.

**Remediation:** Pass bar resolution into `SimulationRunner` and compute warmup bars as `lookback_days * bars_per_day[resolution]`. Add a `BarResolution` field to `SimulationRunRequest`.

---

### Finding R-07 — Limit Order Fill Model Is Too Optimistic

**Severity: Medium**
**Affected components:** `SimulatedExecutionService`

Limit buy orders fill if `bar.low <= limit_price`. Limit sell orders fill if `bar.high >= limit_price`. This model fills limit orders at `limit_price` regardless of where within the bar's range the price touched the limit.

This creates several issues:
1. **Price improvement is never modeled.** When `bar.low` is well below `limit_price`, the fill should occur at `limit_price`, but in many realistic scenarios the fill occurs at a better price due to order queue position.
2. **Fill at the very tick of the limit.** When `bar.low == limit_price` exactly, the fill occurs but queue priority means many retail orders would not fill on a single touch.
3. **No consideration of bar open.** If `bar.open < limit_price` (gap down), a limit buy fills at `bar.open`, not `limit_price`, because the market opened below the limit. The current model would fill at `limit_price` even when opening through would have filled better.

**Production consequence:** Limit order strategies will show better fill rates and worse average fill prices in simulation than live. The fill rate overstatement is the larger problem — strategies that rely on limit order selectivity will see their edge diluted live because many simulated fills would simply not occur.

**Remediation:** Implement gap-open fill logic: if `bar.open < limit_buy_price`, fill at `bar.open`. For single-touch limit fills, apply a configurable `fill_probability_on_touch` parameter (e.g., 50%) to model queue position uncertainty.

---

## 3. Failure Scenario Matrix

### Finding F-01 — No Partial Fill Modeling in Simulation

**Severity: High**
**Affected components:** `SimulatedExecutionService`

Every simulated order fills completely (100% quantity) or not at all. The live execution path handles `PARTIALLY_FILLED` state and incremental fills extracted from broker responses. The simulation path never produces a partial fill.

**Production consequence:** Strategies that depend on position sizing robustness are not tested against partial fill scenarios. A strategy that rebalances on the assumption of full execution will have incorrect position tracking when live fills are partial. The divergence between backtest (always full fills) and live (partial fills in less liquid names or during high-volatility periods) is a systematic P&L drag not captured in simulation.

**Remediation:** Add `partial_fill_probability` and `partial_fill_size_distribution` to `SimulatedFillModelConfig`. For volume-capped orders (Finding R-04 fix), partial fills arise naturally from ADV constraints.

---

### Finding F-02 — No Fill Rejection or Order Rejection Modeling

**Severity: Medium**
**Affected components:** `SimulatedExecutionService`

Every submitted order fills in simulation. There is no modeling of:
- Broker rejection (risk check failure, invalid symbol, market closed)
- Exchange rejection (locked/crossed market, circuit breaker)
- Order expiry (DAY orders that do not fill by close)
- Halt-related non-fill (stock in a trading halt)

**Production consequence:** Strategies that would trigger broker-level risk controls in live trading appear viable in simulation. More commonly, DAY limit orders that expire unfilled are counted as fills in simulation, distorting portfolio construction at the next signal cycle.

**Remediation:** Add `rejection_model` to `SimulatedFillModelConfig` with configurable rejection rates. At minimum, model DAY order expiry: limit orders that do not fill within the bar's price range expire at end of bar and are not carried forward.

---

### Finding F-03 — Duplicate Order Submission on Retry

**Severity: High**
**Affected components:** `OrderExecutionService`

`OrderExecutionService.submit()` uses exponential backoff with up to 3 retry attempts. If the first submission succeeds at the broker but the HTTP response is lost (network timeout, connection reset), the retry will re-submit the same order. The `client_order_id` is used as an idempotency key, but this protection only works if Alpaca deduplicates on `client_order_id` — which it does for the Alpaca API, but this assumption is embedded only in the broker adapter, not enforced with verification.

The retry logic does not distinguish between "failed to reach broker" (safe to retry) and "broker received but response lost" (retry creates duplicate). The current implementation retries all exceptions identically.

**Production consequence:** In an outage scenario, the platform could submit the same order multiple times, doubling or tripling position exposure. This is a real production risk for any systematic trading platform.

**Remediation:** Before retry, attempt `get_order_by_client_order_id(client_order_id)` to check if the broker already has the order. If found, skip re-submission and proceed with the existing broker order. Only retry if the broker confirms no existing order with that `client_order_id`.

---

### Finding F-04 — Race Condition in Two-Phase Fill Quality Recording

**Severity: Medium**
**Affected components:** `RealisedSlippageService`

Phase 1 (`record_submission_context`) persists pre-execution data immediately after submit. Phase 2 (`record_fill_actuals`) updates the row when the fill is reconciled. If a fill arrives at the broker between submit and Phase 1 persistence (possible during high-throughput or DB contention), Phase 1 write may attempt to INSERT a row that Phase 2 is simultaneously trying to UPDATE, or Phase 2 may arrive before Phase 1 has completed.

**Production consequence:** Fill quality records may have missing Phase 1 data, making slippage analytics incomplete. In extreme cases, Phase 2 attempts to UPDATE a non-existent row, silently dropping the fill quality record.

**Remediation:** Use upsert semantics (INSERT ... ON CONFLICT DO UPDATE) for both phases, keyed on `intent_id + fill_id`. Alternatively, buffer Phase 1 data in memory and perform a single write when Phase 2 data is available.

---

### Finding F-05 — Out-of-Order Broker Updates Can Corrupt Fill Extraction

**Severity: Medium**
**Affected components:** `BrokerOrderMapper.extract_incremental_fill()`

Incremental fill detection compares `current.filled_qty > previous.filled_qty`. If broker status updates arrive out of order (previous state has higher `filled_qty` than current due to message ordering, retry, or cache staleness), `filled_qty_delta` will be negative or zero, silently dropping fills. There is no fill sequence number from the broker to detect gaps.

**Production consequence:** Fills are silently dropped from the platform's records while the broker books them. Position and cash ledgers diverge from broker state. The external reconciliation service would eventually detect the drift, but the window between fill drop and reconciliation represents a period of incorrect portfolio state.

**Remediation:** Add defensive check: log a `CRITICAL` event if `current.filled_qty < previous.filled_qty`. Consider storing all raw broker response snapshots (already partially addressed via `raw_broker_payload`) with timestamps to enable replay and gap detection.

---

### Finding F-06 — T+2 Settlement Not Modeled

**Severity: Medium**
**Affected components:** `CashLedgerService`, `SimulationExecutionEngine`

Cash from sell fills is immediately added to buying power in both simulation and live paths. US equities settle T+2 (equities) or T+1 (moving to T+1 as of 2024). Selling a position and immediately buying with the proceeds is not possible in a cash account, and in a margin account it consumes margin that may not exist.

**Production consequence:** The simulation overstates available capital for strategies that rapidly cycle positions (sell → buy same day). This leads to more aggressive sizing in simulation than is achievable live in a cash account. For margin accounts the impact is lower but not zero.

**Remediation:** Add `settlement_days` parameter to `CashLedgerService`. Pending settlement cash should be tracked separately from settled cash. Buying power computation should exclude unsettled proceeds.

---

## 4. Divergence Between Runtime Modes

### Finding D-01 — Execution Policy Engine Is Not Applied During Simulation

**Severity: High**
**Affected components:** `ExecutionPolicyEngine`, `SimulationExecutionEngine`

The `ExecutionPolicyEngine` (TWAP, VWAP-lite, LIMIT with offset, slippage pre-computation) is a live/paper-only concern. Simulation bypasses it entirely. A strategy configured to use `TWAP` execution in live trading will have its backtest filled as a single market order at bar close.

This creates a fundamental consistency gap: the cost assumptions and execution mechanics of live trading are not replicated in simulation, so backtest results are not a valid prediction of live performance for any non-PASSTHROUGH policy.

**Production consequence:** TWAP/VWAP strategies appear better in backtest (one fill at close vs. multiple fills spread across a window with varying prices) or worse depending on market direction during the slice window. There is no way to assess live vs. simulation divergence from the current architecture.

**Remediation:** Introduce an `IExecutionModel` interface with `SimulatedExecutionModel` and `LiveExecutionModel` implementations. `SimulatedExecutionModel` for TWAP should spread fills across N bars using the volume profile, applying per-slice slippage. This is a medium-complexity addition that significantly improves backtest validity for execution-policy-aware strategies.

---

### Finding D-02 — Paper Trading and Live Trading Use Identical Broker Path

**Severity: Low / Informational**
**Affected components:** `OrderExecutionService`, `AlpacaBrokerClient`

Paper trading routes through the real Alpaca paper trading API, not through `SimulatedExecutionService`. This is the correct architecture for paper trading. However, it means "paper trading" and "simulation" are completely different execution mechanisms with no shared fill model validation. Results from one do not calibrate the other.

**Production consequence:** This is architecturally sound but means paper trading fill quality depends on Alpaca's paper trading matching engine (which may use different liquidity than live), and simulation fill quality depends on the simplified model. There is no explicit mechanism to compare paper fills vs. simulation fills to validate the model.

**Remediation:** Add a `SimulationVsPaperComparison` report to `RealisedSlippageService` that compares actual paper fills against what the simulation model would have predicted for the same orders. This enables model calibration without live capital risk.

---

### Finding D-03 — No Shadow Mode / Side-by-Side Simulation

**Severity: Medium**
**Affected components:** Architecture-level gap

There is no mechanism to run a strategy in simulation alongside live/paper execution on the same bar data, comparing what the simulation "would have done" vs. what live execution actually did. This is a standard institutional practice for model validation.

**Production consequence:** Model drift between simulation and live goes undetected until significant P&L divergence accumulates. The platform has the components needed (simulation engine + live execution) but no wiring to run them side-by-side.

**Remediation:** Add a `shadow_run_id` concept to `SimulationRunRequest` that links a simulation run to a live `run_id`. Post-run comparison reports should compare trade lists, fill prices, and equity curves between shadow and live.

---

## 5. Determinism and Reproducibility Risks

### Finding P-01 — UUID4 Fill IDs Are Non-Deterministic

**Severity: High**
**Affected components:** `SimulatedExecutionService._create_fill()`

Simulated `fill_id` and `broker_order_id` are generated with `uuid4()`, which is not seeded. Even with a fixed `random_seed`, two simulation runs with identical inputs will produce different fill IDs. This prevents:
- Exact artifact comparison between runs
- Idempotent re-runs that produce identical storage records
- Fill-level diffing between simulation variants

**Production consequence:** Re-running a simulation with the same seed produces different artifact content at the fill ID level. Downstream analytics that join on fill IDs across runs will fail. The `SimulationArtifactIdentity` partition scheme provides identity at the run level but not fill level.

**Remediation:** Use `uuid5(namespace, deterministic_key)` where `deterministic_key = f"{run_id}:{intent_id}:{bar_timestamp}:{symbol}:{side}:{fill_index}"`. This produces stable fill IDs that are deterministic across re-runs of the same simulation.

---

### Finding P-02 — Global Random State Is Not Isolated

**Severity: Medium**
**Affected components:** `SimulationRunner._set_seed()`

`random.seed()` and `np.random.seed()` set global module-level state. Any library dependency that calls `random` or `numpy.random` between simulation runs will alter the state. This is a known limitation of global RNG seeding: it provides weaker guarantees than isolated RNG instances.

Additionally, `pandas` operations that internally use randomness (e.g., `sample()`, some `groupby` operations in certain pandas versions) are not seeded. `datetime.now()` and `uuid4()` calls in the execution path are inherently non-deterministic.

**Production consequence:** Simulation outputs may not be exactly reproducible across Python/library version upgrades, even with the same seed. This complicates long-term research integrity.

**Remediation:** Pass an explicit `numpy.random.Generator` instance (constructed from the seed) into all components that need randomness. Avoid global `random.seed()` in favor of a `Random` instance passed via dependency injection. This is a lower-priority hardening item.

---

### Finding P-03 — No Determinism Validation in CI

**Severity: Medium**
**Affected components:** `SimulationRunner`, test suite

There are no tests that run the same simulation twice with the same seed and assert identical output. The determinism tracking fields in `run_manifest` record intent but do not verify it.

**Production consequence:** Determinism regressions (from library upgrades, code changes, non-seeded RNG calls) are silent. A strategy that appears reproducible will silently become non-reproducible after a dependency update.

**Remediation:** Add a CI test that runs a fixed simulation twice with the same seed and asserts `equity_curve == equity_curve` and `trade_log == trade_log` element-wise. Flag this as a regression gate.

---

## 6. Accounting Integrity Risks

### Finding A-01 — No FIFO/LIFO Cost Basis Tracking

**Severity: Medium**
**Affected components:** `PositionLedgerService`

`PositionLedgerService` uses weighted average cost for all positions. US tax accounting (and some institutional accounting) requires FIFO or LIFO lot tracking. More importantly for simulation realism: average cost masks the P&L impact of partial position exits, particularly for strategies that build positions in tranches.

**Production consequence:** Realized PnL reported in simulation may differ from what a real brokerage would report, complicating strategy performance verification. For simulation purposes, average cost is generally acceptable but should be documented as a known simplification.

**Remediation:** Document the weighted-average-cost assumption explicitly in `PositionLedgerService`. For institutional hardening, add a `lot_tracking` flag that enables FIFO accounting.

---

### Finding A-02 — No Dividend or Corporate Action Accounting

**Severity: Medium**
**Affected components:** `SimulationExecutionEngine`, `CashLedgerService`

Simulation ignores dividends and corporate actions (splits, mergers, spin-offs). For a backtest spanning multiple years, dividend income represents a material return component for income-oriented strategies and for any strategy holding positions through ex-dividend dates. Stock splits cause apparent price dislocations that would distort technical indicators in simulation.

**Production consequence:** Total return is understated for dividend-paying securities. Strategies holding through ex-dates will show incorrect position economics. Split-adjusted price series (if the data provider supplies them) mask this issue for price continuity but not for cash accounting.

**Remediation:** Add `dividend_events` to `SimulationRunRequest`. Apply dividend cash to `CashLedgerService` on ex-date. Verify that price data sourced from the ingestion layer is split-adjusted and note this in the simulation manifest.

---

### Finding A-03 — Reserved Cash Accounting Is Unclear

**Severity: Low**
**Affected components:** `CashLedgerService`

`CashLedgerResult` includes a `reserved_cash` field but the mechanism that populates it is not visible in the inventory. It is unclear whether pending orders (submitted but not yet filled) reserve cash, and whether this reserved cash is correctly deducted from buying power during simulation.

**Production consequence:** If `reserved_cash` is not populated for pending simulation orders, the simulation may allow over-allocation: two orders could both claim the same cash pool before either fills, leading to negative cash in the portfolio.

**Remediation:** Clarify and test the `reserved_cash` lifecycle. Ensure that `OrderSimulatorService` reserves cash at order generation time and that `CashLedgerService` releases the reservation on fill or cancellation.

---

## 7. Event Ordering and Idempotency Risks

### Finding E-01 — Reconciliation Is Pull-Based With No Fill Gap Detection

**Severity: Medium**
**Affected components:** `OrderReconciliationService`, `ExternalBrokerReconciliationService`

The platform reconciles by polling the broker. There is no fill stream subscription (websocket/webhook). Fills that occur between reconciliation polls are accumulated correctly (incremental fill extraction from `filled_qty` delta), but if a broker order is completed and then disappears from the API response before a reconciliation poll (e.g., Alpaca purges old orders from the open order list), fills could be missed.

**Production consequence:** In fast-moving markets or when polls are delayed (system busy, restart), fills could be missed. Missed fills lead to position and cash ledger divergence that persists until the external reconciliation service detects the discrepancy — potentially not until the next full reconciliation cycle.

**Remediation:** Subscribe to Alpaca's websocket order update stream as the primary fill notification channel. Use polling reconciliation as a backstop, not the primary mechanism.

---

### Finding E-02 — State Machine Missing EXPIRED and PENDING_CANCEL States

**Severity: Low**
**Affected components:** `OrderStateMachineService`, `OrderStatus` enum

The state machine transitions are `NEW → SUBMITTED → PARTIALLY_FILLED/FILLED/CANCELED/REJECTED`. Missing states:
- `EXPIRED`: DAY orders that expire at market close without filling
- `PENDING_CANCEL`: Order cancellation submitted but not yet confirmed
- `PENDING_NEW`: Order acknowledged by platform but not yet submitted to broker

Without `EXPIRED`, DAY limit orders that never fill are left in `SUBMITTED` state indefinitely, polluting the open order list and potentially triggering repeated reconciliation attempts.

**Production consequence:** The open order count inflates over time as expired orders accumulate in non-terminal states. The reconciliation service would need special handling for "order no longer exists at broker" to avoid treating a disappeared order as a failure.

**Remediation:** Add `EXPIRED` and `PENDING_CANCEL` to `OrderStatus`. Add broker-side "order not found" handling in `OrderReconciliationService` that transitions to `EXPIRED` when a broker order ID returns 404.

---

## 8. Calibration Limitations

### Finding C-01 — Slippage Model Is Not Calibrated From Live Data

**Severity: High**
**Affected components:** `SlippageConfig`, `SimulationCostModelService`, `RealisedSlippageService`

`RealisedSlippageService` captures actual fill quality data with full detail (bps, is_adverse_fill, fill_vs_expected_bps, submission_latency). However, there is no pipeline that feeds this data back into `SlippageConfig` to update the simulation model. The simulation continues using the hardcoded `slippage_rate = 0.0001` regardless of what actual execution data shows.

**Production consequence:** The simulation and live execution models drift over time. Measured slippage changes with market conditions (volatility regimes, time of day, liquidity periods) but the simulation model is static. Research integrity degrades as the gap widens.

**Remediation:** Build a `SlippageCalibrationService` that reads `FillQualityRecord` aggregates (by symbol, order size tier, time-of-day bucket) and publishes updated `SlippageConfig` objects. Trigger re-calibration monthly or after significant market regime changes. Store calibration history with timestamps.

---

### Finding C-02 — Adverse Fill Threshold Is Hardcoded and Not Strategy-Aware

**Severity: Low**
**Affected components:** `RealisedSlippageService` (`_ADVERSE_SLIPPAGE_THRESHOLD_BPS = 10`)

The 10 bps adverse fill threshold is a module-level constant, not configurable per strategy or per execution policy. A TWAP strategy might accept 5 bps as adverse while a market-order strategy might only flag 25 bps. The current threshold produces alerts that may be meaningless noise for some strategies and miss real problems for others.

**Production consequence:** Operators receive adverse fill alerts that don't correspond to actual strategy-level problems, leading to alert fatigue and eventual disregard of the signal.

**Remediation:** Move `_ADVERSE_SLIPPAGE_THRESHOLD_BPS` to `ExecutionPolicyConfig` so it is strategy-specific and surfaced in the simulation manifest for comparison.

---

## 9. Prioritized Remediation Roadmap

### Tier 1 — Critical (Fix Before Trusting Research Results)

| ID | Finding | Effort | Impact |
|----|---------|--------|--------|
| R-01 | CURRENT_CLOSE look-ahead bias → implement NEXT_OPEN fill policy | Medium | Eliminates systematic upward bias in all daily bar backtests |
| R-04 | No ADV/liquidity cap → add volume participation constraint | Medium | Prevents impossible fills, enables capacity analysis |
| F-01 | No partial fill modeling → add partial fill probability | Low–Medium | Closes largest live-vs-sim behavioral gap |
| P-01 | Non-deterministic fill IDs → use uuid5 with deterministic key | Low | Enables idempotent re-runs and fill-level diffing |

### Tier 2 — High (Fix Before Production Capital Deployment)

| ID | Finding | Effort | Impact |
|----|---------|--------|--------|
| R-02/R-03 | Wire VolumeShareSlippageModel into simulation path | Medium | Eliminates silent use of wrong slippage model |
| F-03 | Retry can duplicate order submission | Low | Prevents accidental double-ordering in live |
| D-01 | Execution policy not applied in simulation | High | Major consistency gap for TWAP/LIMIT strategies |
| C-01 | Slippage model not calibrated from live data | Medium | Research model stays synchronized with reality |
| F-06 | T+2 settlement not modeled | Low | Prevents over-allocation in cash account strategies |

### Tier 3 — Medium (Operational Hardening)

| ID | Finding | Effort | Impact |
|----|---------|--------|--------|
| R-05 | Zero-latency simulation → add latency_bars | Low | Important for intraday strategies |
| R-06 | Hardcoded 78 bars/day warmup → parameterize by resolution | Low | Correctness for daily and 1-min bar strategies |
| R-07 | Limit order fill model too optimistic | Low–Medium | Improves fill rate realism for limit-order strategies |
| F-02 | No order rejection modeling | Medium | Tests strategy resilience to non-fills |
| F-04 | Race condition in two-phase fill quality recording | Low | Prevents silent data loss in analytics |
| F-05 | Out-of-order broker updates corrupt fill extraction | Low | Defensive integrity guard |
| E-01 | Pull-based reconciliation → add websocket fill stream | High | Reduces fill gap window from minutes to milliseconds |
| E-02 | Missing EXPIRED/PENDING_CANCEL states | Low | Prevents open order list pollution |
| P-02 | Global RNG state → isolated RNG instances | Medium | Long-term reproducibility hardening |
| P-03 | No determinism CI test | Low | Prevents silent determinism regressions |

### Tier 4 — Lower Priority (Institutional Hardening)

| ID | Finding | Effort | Impact |
|----|---------|--------|--------|
| A-02 | No dividend/corporate action accounting | High | Total return accuracy for multi-year backtests |
| D-02 | No paper vs. simulation comparison pipeline | Medium | Validates simulation model against paper fills |
| D-03 | No shadow mode execution | High | Enables continuous model validation against live |
| C-02 | Hardcoded adverse fill threshold | Low | Reduces alert fatigue |
| A-01 | No FIFO lot tracking | High | Tax and institutional reporting (not needed for research) |
| A-03 | Reserved cash lifecycle clarity | Low | Prevents potential over-allocation in simulation |

---

## Summary

The platform has a solid structural foundation: the layered architecture is clean, the ledger services are shared between simulation and live (good), the two-phase fill quality recording is a thoughtful design, and the safety gate system is thorough. The primary risks concentrate in **fill model realism** and **mode consistency**:

The most consequential single issue is **CURRENT_CLOSE fill policy (R-01)**. Every backtest on daily bars has a structural look-ahead bias baked in at the fill model level. Until `NEXT_OPEN` is implemented, simulation results overstate achievable live performance by a margin that varies with strategy type but is always positive.

The second priority cluster is **the disconnected slippage model (R-02/R-03)** — the better model exists but is never called — combined with **no ADV liquidity cap (R-04)** — the simulation allows physically impossible fill quantities. These two issues together mean capacity analysis and cost analysis from simulation are unreliable.

The third cluster is **mode consistency (D-01)**: the execution policy engine (TWAP/LIMIT) has no simulation analog. Strategies validated in simulation with one execution assumption are then run live with a different one, and there is no systematic way to compare the two.
