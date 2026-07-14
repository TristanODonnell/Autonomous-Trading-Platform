# Audit: execution/, risk/, portfolio/

Date: 2026-07-07. Auditor scope: `src/autonomous_trading_platform/execution/`, `risk/`, `portfolio/`. Every file read; no sampling.

## Verified counts

Command:
```
for d in execution risk portfolio; do find src/autonomous_trading_platform/$d -type f -name '*.py' | sort | xargs wc -l; done
```
Output (totals): execution = 48 files, 8,095 LOC; risk = 7 files, 1,009 LOC; portfolio = 6 files, 1,008 LOC. (Grand total 61 files, 10,112 LOC; includes 9 zero-byte `__init__.py` files: execution root + clients, contexts, jobs, mappers, policy, services subpackages, risk root, portfolio root.)

TODO/FIXME/XXX:
```
grep -rnE 'TODO|FIXME|XXX' src/autonomous_trading_platform/execution src/autonomous_trading_platform/risk src/autonomous_trading_platform/portfolio | wc -l
-> 0
```

## Per-file entries

### execution/__init__.py, clients/__init__.py, contexts/__init__.py, jobs/__init__.py, mappers/__init__.py, policy/__init__.py, services/__init__.py (0 lines each)
- Purpose: empty package markers. Note: `execution/jobs/` contains ONLY an empty `__init__.py` — no job modules live here (order submission jobs live elsewhere, referenced as `run_order_submission_job` in policy engine docstring).

### src/autonomous_trading_platform/execution/clients/alpaca_broker_client.py (274 lines)
- Purpose: httpx-based Alpaca REST client (orders, account, positions, latest quotes/trades via IEX feed) with full OTel instrumentation.
- Notable: validates base URL against an allowlist of paper/live URLs at construction (defense against typo'd endpoints); `get_order_by_client_order_id` exists specifically "for idempotency verification before retrying ambiguous submissions" (404 -> None); per-request `X-RATP-Request-ID` UUID header; latency/failure/request counters + spans per endpoint tag. Substantive, production-shaped client.

### src/autonomous_trading_platform/execution/clients/alpaca_order_stream_client.py (89 lines)
- Purpose: async wrapper around alpaca-py `TradingStream` that normalizes trade-update events into plain dicts and forwards to a callback.
- Notable: calls the private `stream._run_forever()` coroutine (acknowledged in comment) — fragile coupling to alpaca-py internals; stamps `stream_received_at` for latency accounting; swallows per-event handler exceptions with logging so one bad event doesn't kill the stream.

### src/autonomous_trading_platform/execution/clients/simulated_broker_client.py (387 lines)
- Purpose: drop-in replacement for AlpacaBrokerClient in backtest replay; fills orders synchronously against the current tick's Parquet OHLCV bar via the research pipeline's SimulatedExecutionService, returning Alpaca-format dicts.
- Notable: strong design — reuses the *same* fill engine as research sims so backtest and research fills agree; duck-typed `_IntentProxy`/`_BarProxy`; reads equity/positions from DB snapshots so sizing works off true net worth; per-tick bar cache + `advance_to()` for intraday replay; falls back adjusted_bars -> raw_bars datasets filtered to `validation_status == "validated"`. Smell: several broad `except Exception: pass` blocks around DB/Parquet reads.

### src/autonomous_trading_platform/execution/contexts/build_execution_context.py (146 lines)
- Purpose: composition root wiring ~18 execution services (broker client, policy engine, slicers, sizers, ledgers, reconciliation) into an ExecutionContext.
- Notable: runs BrokerStartupHealthCheckService before returning a live client; accepts injected `broker_client` (how SimulatedBrokerClient swaps in). Manual DI, no framework.

### src/autonomous_trading_platform/execution/contexts/execution_context.py (57 lines)
- Purpose: dataclass aggregating the 14 wired execution services for handoff to jobs/runtime.

### src/autonomous_trading_platform/execution/errors.py (30 lines)
- Purpose: execution-domain exception hierarchy (invalid order/strategy transitions, submission blocked, broker health-check/credential failures, runtime-sync failure, broker 404 during reconciliation).
- Notable: exception names encode the fail-closed doctrine (e.g. `BrokerStartupHealthCheckError` "fail closed").

### src/autonomous_trading_platform/execution/mappers/broker_order_mapper.py (301 lines)
- Purpose: maps Alpaca payloads -> BrokerOrder contract -> ORM rows; maps broker status -> internal OrderStatus -> OrderEvent; extracts incremental fills from cumulative filled_qty.
- Notable: DUPLICATE-FILL / STALE-UPDATE HEURISTICS LIVE HERE: `extract_incremental_fill` computes delta vs previous filled_qty; delta<0 => logs CRITICAL "fill.quantity_regression_detected" and preserves monotonic state; delta==0 => logged as duplicate broker update, no fill emitted. Nuanced status mapping (done_for_day/expired vs canceled; suspended->REJECTED); raises on unknown broker status rather than guessing. Caveat: incremental fill price uses cumulative `avg_fill_price`, not the true per-slice price (approximation for partial fills).

### src/autonomous_trading_platform/execution/policy/errors.py (20 lines)
- Purpose: policy-layer exceptions (missing market data, unsupported policy mode, invalid slice config).

### src/autonomous_trading_platform/execution/policy/execution_policy_engine.py (267 lines)
- Purpose: orchestrates execution policy per OrderIntent: fetch quote -> resolve market/limit -> build TWAP/VWAP-lite slice schedule -> compute expected slippage + cost breakdown -> return ExecutionPolicyResult.
- Notable: VERIFIES claimed TWAP/VWAP-lite + slippage modeling wiring. Fail-soft posture: quote-fetch failure degrades to market order + skipped analytics rather than blocking the trade; explicit docstring scoping what it does NOT do (submit, persist, risk-check). Calls private `OrderTypeResolver._force_market` cross-class (minor smell).

### src/autonomous_trading_platform/execution/policy/i_execution_model.py (37 lines)
- Purpose: `IExecutionModel` runtime-checkable Protocol — plan() maps a parent intent to (bar_offset, child_intent) pairs, shared between simulation and live paths.
- Notable: contract requires PASSTHROUGH to return [(0, intent)] so callers can use it unconditionally.

### src/autonomous_trading_platform/execution/policy/order_type_resolver.py (114 lines)
- Purpose: decides market vs limit per policy mode; computes limit prices as mid ± offset_bps with ROUND_HALF_UP to 4dp.
- Notable: correct microstructure framing (buy limit above mid = "passive-but-reachable" price protection); PASSTHROUGH leaves strategy's order type untouched; raises MissingMarketDataError rather than inventing a limit price.

### src/autonomous_trading_platform/execution/policy/slippage_calculator.py (226 lines)
- Purpose: pre-execution slippage estimate (fixed rate applied directionally to mid), actual (post-fill) slippage with signed adverse convention, and CostBreakdown = commission + half-spread + slippage notional.
- Notable: VERIFIES slippage modeling claim; taker-pays-half-spread modeling; `volume_based_rate()` = participation-scaled v2 extension point (linear in order_qty/ADV); all Decimal with explicit quantization. Smell: `_effective_rate` contains a dead conditional block whose body is `pass` (spread deliberately kept out of the rate but code shape suggests leftover indecision).

### src/autonomous_trading_platform/execution/policy/twap_slicer.py (123 lines)
- Purpose: splits an order into equal time-weighted OrderSlice buckets (floor division, remainder to last slice) across window_minutes.
- Notable: VERIFIES TWAP claim with an honest architecture note: Alpaca receives ONE parent order; the slice schedule is embedded in intent metadata for audit/analytics and future child routing — i.e. TWAP is a *schedule*, not actual child-order submission, in live mode. Limit-slice prices computed at mid ± offset. Notional-only intents skip slicing (qty unknown pre-fill).

### src/autonomous_trading_platform/execution/policy/vwap_lite_slicer.py (123 lines)
- Purpose: slices proportional to a static intraday volume profile — `uniform` or `u_shaped` ((|i-mid|+1)^2 normalized), modeling the classic U-shaped US-equity intraday volume curve.
- Notable: VERIFIES VWAP-lite claim; same "schedule not child orders" architecture as TWAP; last-slice residual absorption guarantees weights sum to 1 and quantities sum to total. Domain knowledge: U-curve rationale documented.

### src/autonomous_trading_platform/execution/services/broker_adaptor.py (60 lines)
- Purpose: `AlpacaBrokerAdapter.to_payload()` translates an internal `OrderIntent` into an Alpaca REST order-submission payload dict.
- Notable: v1 constraints enforced defensively: only market/limit supported, extended-hours orders must be limit, fractional-qty orders must be DAY TIF (Alpaca hard requirement) — validated via `Decimal.to_integral_value()` comparison before submission rather than trusting upstream.

### src/autonomous_trading_platform/execution/services/broker_event_stream_service.py (160 lines)
- Purpose: manages the Alpaca order-update websocket connection lifecycle with exponential backoff reconnects (1s -> 60s, x2 multiplier).
- Notable: explicit architecture doc comment: websocket stream = primary near-real-time path, polling reconciliation = secondary backstop, both converge on `BrokerStreamFillProcessor`'s idempotent delta-qty extraction so a fill can't double-count regardless of source. Clean reconnect loop keyed off `asyncio.Event` for graceful stop; per-event handler exceptions are caught/logged so one bad payload doesn't kill the stream.

### src/autonomous_trading_platform/execution/services/broker_runtime_sync_service.py (490 lines)
- Purpose: pulls broker (Alpaca) account/cash/positions/open-orders/order-status/fills into persisted SoR snapshots (`BrokerAccountSnapshot`, `CashSnapshot`, `PositionSnapshot`, `BrokerOrder`, `Fill`) via `SorUnitOfWork`.
- Notable: `validate_broker_runtime_consistency()` compares persisted vs live broker cash/buying_power/equity/portfolio_value within a configurable Decimal tolerance (default 0.01) and returns a structured mismatch list — a real drift-detection mechanism, not a stub. `reconcile_order_fills()` reuses `BrokerOrderMapper.extract_incremental_fill` (same dedup mechanism as the mapper) and records signal->submit->ack->fill latency splits via `record_order_execution_latency`. Docstring is candid that positions/open-orders/fills sync were "narrow stubs until integration points are clarified" — yet the actual methods below (`sync_positions_from_broker`, `sync_open_orders_from_broker`, `sync_order_status`, `reconcile_order_fills`) are fully implemented, so the docstring appears stale/out of date relative to the code.

### src/autonomous_trading_platform/execution/services/broker_startup_health_check_service.py (55 lines)
- Purpose: calls `broker_client.get_account()` at startup and fails closed (raises `InvalidBrokerCredentialsError` on 401/403, `BrokerStartupHealthCheckError` otherwise) before the runtime is allowed to trade live.
- Notable: small, focused, correctly distinguishes credential failures from generic connectivity failures for operator triage.

### src/autonomous_trading_platform/execution/services/broker_stream_fill_processor.py (186 lines)
- Purpose: processes one normalized websocket order-update event into a fill/status transition, using local `ReconciliationInput` tracking state (previous filled qty/avg price/current status) for idempotent extraction.
- Notable: reuses `BrokerOrderMapper.extract_incremental_fill` (shared dedup logic with the polling path — the SAME mechanism claimed for duplicate-fill detection); tags emitted fills with `update_source="stream"` + `stream_received_at` metadata for traceability; skips (with reason codes `missing_broker_order_id` / `order_not_tracked`) rather than guessing when local state is absent — fail-safe design that avoids fabricating fills for untracked orders.

### src/autonomous_trading_platform/execution/services/cash_ledger_service.py (179 lines)
- Purpose: pure-function cash ledger: `apply_fill` (BUY debits settled cash + releases reservation; SELL credits proceeds to settled or unsettled bucket per `settlement_days`), `reserve_order` (pre-trade buying-power reservation, raises if insufficient), `release_reservation` (order cancel/reject/expire).
- Notable: correctly models T+settlement mechanics — SELL proceeds can be routed to an `unsettled_cash` bucket that does not count toward buying power until matured, a real brokerage-accounting nuance; BUY reservation release is `min(pool, fill_notional)` against an aggregate (not per-order) reservation pool, which is a simplification (no per-order reservation tracking) but internally consistent and documented. All Decimal, validates positivity/non-negativity of every input.

### src/autonomous_trading_platform/execution/services/drawdown_scaling_service.py (248 lines)
- Purpose: computes a `drawdown_scalar` in [0,1] from `realized_drawdown / max_drawdown_allowed` utilization, tapering position size as a strategy approaches its configured max-drawdown limit; supports linear/exponential/sigmoid curve shapes.
- Notable: VERIFIES "dynamic position scaling by drawdown" claim — substantive, not a stub. Explicitly designed to compose with `VolatilityScalingService`/`SharpeScalingService` scalars inside `PositionSizer` (documented shared design). Fail-open on misconfiguration (`max_drawdown_allowed <= 0` logs a warning and returns scalar=1.0 rather than raising/dividing by zero) — a deliberate but debatable choice (silently disables risk control on bad config rather than blocking). Hard limit (utilization>=1.0) always forces scalar=0 regardless of curve or floor.

### src/autonomous_trading_platform/execution/services/external_broker_reconciliation_service.py (517 lines)
- Purpose: read-only comparison of platform SoR state vs live Alpaca broker state across orders/fills/positions/cash/equity, producing a `ReconciliationReport` with per-check severity (INFO/WARNING/CRITICAL) and overall PASSED/DRIFTED/FAILED status.
- Notable: VERIFIES duplicate-fill-detection claim, though the mechanism here is a coarse proxy: "possible duplicate" fills are inferred from platform-tracked orders with `previous_filled_qty > 0` that are absent from the broker's open-order list (comment admits detailed per-order comparison would need extra broker API calls). Tiered thresholds are concrete and sensible: cash/equity WARNING at $1, CRITICAL at $100; position qty WARNING at 1 share, CRITICAL at 10 shares (sub-share deltas treated as rounding/INFO). Emits OTel counters (`ratp_duplicate_fills_detected_total`, `ratp_equity_drift_amount`, `ratp_unreconciled_orders`) tagged by environment — real observability wiring, not decorative. All broker fetches individually try/except -> None with graceful FAILED-check degradation rather than blowing up the whole reconciliation pass.

### src/autonomous_trading_platform/execution/services/order_execution_service.py (211 lines)
- Purpose: submits `OrderIntent` -> broker payload with retry/backoff (default 3 attempts, exponential backoff from 0.5s) and OTel spans; on ambiguous transport-level failures, performs an idempotency lookup via `client_order_id` before retrying.
- Notable: VERIFIES SHA-256/idempotency-adjacent claim at the transport-retry layer specifically — `_lookup_existing_broker_order` calls `get_order_by_client_order_id` to check whether an ambiguous (transport-error) submit actually reached the broker before blindly resubmitting, preventing duplicate live orders; if the idempotency lookup itself fails, it re-raises rather than guessing (fail-closed). Delegates to `PaperRuntimeOrderSafeguardService.assert_payload_allowed` before every submit. Note: the actual client_order_id value/uniqueness generation (hashing scheme) is NOT in this file — must check `OrderIntent` contract / wherever `client_order_id` is minted to verify the "SHA-256-keyed" claim. RESOLVED below: `src/autonomous_trading_platform/safety/services/order_idempotency_service.py` (out of assigned scope, checked for completeness) builds `idempotency_key = sha256("|".join([run_id, strategy_id, bar_timestamp.isoformat(), symbol.upper(), side, qty])).hexdigest()` and checks `order_activity_reader.idempotency_key_exists_between(...)` within a configurable time window — VERIFIES SHA-256-keyed idempotency as a real, substantive mechanism (not a stub), though it lives in `safety/`, not `execution/`.

### src/autonomous_trading_platform/execution/services/order_reconciliation_service.py (197 lines)
- Purpose: fetches one broker order by `broker_order_id` and reconciles it against locally tracked state (`ReconciliationInput`), extracting incremental fills and driving `OrderStateMachineService` transitions; treats a 404 (or simulated-broker `{"status": "not_found"}`) from an expirable state as an EXPIRE transition.
- Notable: `_TERMINAL_STATUSES`/`_EXPIRABLE_STATUSES` partition is used to decide when a missing broker order is legitimately "it expired" vs an error; explicitly detects and logs (but does not block on) status regressions such as FILLED -> PARTIALLY_FILLED, which would indicate a serious broker data inconsistency.

### src/autonomous_trading_platform/execution/services/order_runtime_state_service.py (148 lines)
- Purpose: persists `TrackedOrder` rows across submission and reconciliation, translating `ReconciliationResult` into SoR updates.
- Notable: `apply_reconciliation_result` enforces a monotonic quantity guard — if a broker snapshot reports `filled_qty` lower than the previously recorded value, it logs `runtime_state.monotonic_qty_guard_triggered` and refuses to rewind, keeping the previous (higher) value. This is a second, independent monotonicity safeguard alongside the mapper's delta-based dedup.

### src/autonomous_trading_platform/execution/services/order_state_machine_service.py (97 lines)
- Purpose: VERIFIES the order lifecycle state machine claim — explicit `VALID_TRANSITIONS` table (NEW -> PENDING_NEW/SUBMITTED/REJECTED -> PARTIALLY_FILLED/FILLED/CANCELED/EXPIRED etc.) with `apply_event()` raising `InvalidOrderTransitionError` on any transition not in the table, and every transition audit-logged via `AuditLogRepository`.
- Notable: terminal states (FILLED/CANCELED/REJECTED/EXPIRED) map to `{}` — no further transitions possible, correctly modeling irreversibility.

### src/autonomous_trading_platform/execution/services/paper_runtime_order_safeguard_service.py (131 lines)
- Purpose: an extra fail-closed cap layer specifically for "explicit external paper-runtime validation" — caps qty/notional/limit_price, requires limit orders and an allowed-symbol list, all disabled by default (`enabled=False`).
- Notable: cross-checks `qty * limit_price` against `max_order_notional` even when both are individually within their own caps — catches a combination that individually passes but is jointly too large. Defaults are extremely conservative (max_order_qty=1, max_order_notional=$1.00) — clearly meant as a tripwire/circuit-breaker for a specific validation exercise, not general paper trading.

### src/autonomous_trading_platform/execution/services/portfolio_construction_service.py (563 lines)
- Purpose: the sizing/order-construction pipeline: consumes `Signal`s + current `Position`s + prices, computes target positions via `PositionSizer` (with combined vol/Sharpe scalar), diffs against current positions to get deltas, and builds `OrderIntent`s, running `PreTradeRiskService.assert_order_allowed` before yielding each one.
- Notable: `_compute_combined_scalar` multiplies vol and Sharpe scalars with an explicit `min(combined, ONE)` clamp described as a defensive invariant against future extensions that might loosen individual caps — good forward-looking risk hygiene. In LIVE mode with `require_vol_scalar_for_live=True`, a missing scalar raises `MissingPositionScalingDataError` and blocks the order — VERIFIES fail-closed dynamic position scaling by volatility/Sharpe. `_build_client_order_id` derives a **deterministic uuid5** (NAMESPACE_URL) from `run_id|strategy_id|bar_timestamp|symbol|side|qty`, truncated to 16 hex chars and prefixed with `strategy_id-symbol-` — this is a SEPARATE, non-SHA-256 idempotency key scheme from `OrderIntent.idempotency_key`/`client_order_id`, distinct from the SHA-256 key in `safety/services/order_idempotency_service.py`. Two different idempotency-key mechanisms coexist in the codebase (uuid5-based client_order_id here vs SHA-256-based dedup check in safety/) — worth flagging as a design duplication/potential confusion point rather than a single unified idempotency scheme.

### src/autonomous_trading_platform/execution/services/portfolio_drawdown_governance_service.py (689 lines)
- Purpose: portfolio-level (not per-strategy) drawdown circuit breaker: tracks peak equity, computes drawdown_pct, and on breach applies a configurable governance action (WARN_ONLY / PAUSE_NEW_TRADING / PAUSE_REBALANCING / ACTIVATE_KILL_SWITCH), persisting pause state and audit events; requires explicit operator `acknowledge_breach()` unless `AUTO_RESUME` is configured.
- Notable: largest file in execution/services and the most heavily documented — explicit design-principles docstring (fail-closed on breach, fail-open on infra errors, idempotent single-breach-event emission, monotonically increasing peak equity so a restart doesn't reset drawdown). This is a distinct, higher-level circuit breaker from `DrawdownScalingService` (which tapers per-strategy position size continuously) — the two compose: this one is a hard portfolio-wide gate, the other a smooth per-strategy multiplier. Metrics emission is wrapped in try/except so observability failures can never block governance logic.

### src/autonomous_trading_platform/execution/services/portfolio_signal_aggregator.py (465 lines)
- Purpose: cross-strategy signal netting layer sitting between strategy evaluation and order construction; resolves BUY/SELL conflicts on the same symbol across strategies using a configurable policy (CONSERVATIVE/SUPPRESS_CONFLICTS, DOMINANT/DOMINANT_SIGNAL, PROPORTIONAL/NETTING_ONLY/NET/ALLOCATION_WEIGHTED/CONFIDENCE_WEIGHTED).
- Notable: real multi-strategy portfolio construction concern (wash-trade elimination) rarely implemented in toy platforms; preserves per-strategy attribution (`StrategySignalContribution`) for governance/performance review even when a signal is suppressed or netted; emits OTel metrics for conflicts/suppression/gross-net exposure. `_proportional` computes a confidence/allocation-weighted net score and only emits an order when `|net_score| > near_zero_threshold` (default 0.10), else suppresses — avoids churn from marginal netted signals.

### src/autonomous_trading_platform/execution/services/position_ledger_service.py (127 lines)
- Purpose: pure fill-application logic for a single-symbol position: BUY updates weighted-average cost; SELL computes realized P&L and reduces quantity, fully closing (returns `None` position) when quantity hits zero.
- Notable: explicitly v1-scoped — raises `ValueError`/`NotImplementedError` for selling without an existing long, short positions, and selling through zero ("not supported in v1"). Honest scope limitation rather than silently producing wrong numbers.

### src/autonomous_trading_platform/execution/services/position_sizer.py (245 lines)
- Purpose: converts an `AllocationResult` + optional combined vol/Sharpe scalar + optional drawdown scalar into a whole-share order quantity, applying `max_position_size_usd` and `max_symbol_exposure_usd` caps and a `min_notional_usd` floor before truncating to whole shares (`ROUND_DOWN`).
- Notable: VERIFIES "dynamic position scaling by drawdown/Sharpe/volatility" end-to-end — explicit composition order documented as `final = base * combined_scalar * drawdown_scalar` then capped, applied in that exact sequence in code. Defensive re-validation of `combined_scalar` bounds `(0,1]` even though the upstream caller already clamps it ("final safety net" per docstring) — genuine defense-in-depth, not redundant paranoia given this is a money-sizing function.

### src/autonomous_trading_platform/execution/services/post_fill_accounting_service.py (153 lines)
- Purpose: orchestrates one fill through both `PositionLedgerService` and `CashLedgerService`, then persists a merged `PositionSnapshot` + `CashSnapshot` to the SoR.
- Notable: uses a deterministic `uuid5`-derived snapshot_id keyed on `(run_id, timestamp, source)` so multiple fills landing within the same bar/timestamp merge into a single snapshot row rather than creating duplicates — a sound idempotent-upsert pattern. Comment explains a subtle SQLAlchemy cascade-safety reason for using `get_or_create_header` instead of touching `.positions` directly.

### src/autonomous_trading_platform/execution/services/realised_slippage_service.py (381 lines)
- Purpose: two-phase fill-quality analytics: Phase 1 (`record_submission_context`) writes pre-execution reference price/expected slippage/cost breakdown at submit time; Phase 2 (`record_fill_actuals`) fills in realized fill price/slippage/latency when the fill confirms, computing `is_adverse_fill` against a configurable bps threshold.
- Notable: explicitly handles both phase-ordering anomalies (phase 2 arriving before phase 1, or vice versa) by merging into whichever row exists or inserting a minimal placeholder — "fill data is never silently lost" is an explicit design goal, backed by warning logs (`fill_quality.phase_ordering_anomaly`) for observability. Adverse-fill threshold resolution has a documented fallback chain (stored per-record `policy_metadata` -> hardcoded 10bps with a logged warning) — good traceability for a magic-number fallback.

### src/autonomous_trading_platform/execution/services/risk_snapshot_service.py (229 lines)
- Purpose: computes a point-in-time `RiskSnapshot` (gross/net exposure, leverage, drawdown_pct, per-limit utilization %, block_reasons) from position/cash snapshots plus a `RiskLimitConfig`.
- Notable: VERIFIES configurable unknown-sector policy claim at the config level — `RiskLimitConfig.unknown_sector_policy: str = "reject"` plus `max_sector_exposure_pct: dict[str, float]` and `default_max_sector_exposure_pct`. However, `_build_block_reasons` only checks gross/net exposure and leverage against limits — sector concentration and per-symbol concentration are surfaced in the `utilization` dict (via `PortfolioRiskStateReader`/`SectorExposureReader`) but NOT included in `is_blocked`/`block_reasons` here, meaning this particular service computes sector/symbol utilization for visibility but does not itself gate on them (the actual gating likely lives in `safety/services/pre_trade_risk_service.py`, out of scope — worth cross-checking there for the full pre-trade-check claim).

### src/autonomous_trading_platform/execution/services/sharpe_scaling_service.py (120 lines)
- Purpose: computes a Sharpe-ratio-based position scalar in (0,1] from recent bar closes — full size at/above `target_sharpe` (default 1.0), floor `min_scalar` (default 0.25) at/below zero Sharpe, linear interpolation between.
- Notable: correct annualization convention shared with `VolatilityScalingService` (`BARS_PER_YEAR` from `common.annualisation`); explicit `risk_free_rate` (default 5%) subtracted before annualizing; guards `bar_vol <= 0` (flat/trending prices) by returning scalar=1.0 rather than dividing by zero.

### src/autonomous_trading_platform/execution/services/strategy_runtime_state_service.py (79 lines)
- Purpose: persists per-strategy FSM state (`StrategyRuntimeState` row) driven by `StrategyStateMachineService`, tracking `last_signal_at`/`last_transition_at`/`cooldown_until`.

### src/autonomous_trading_platform/execution/services/strategy_state_machine_service.py (55 lines)
- Purpose: a SEPARATE state machine (from `OrderStateMachineService`) for strategy-level lifecycle: IDLE -> SIGNALLED -> PENDING -> IN_POSITION -> EXIT_PENDING -> COOLDOWN -> IDLE, with several documented "re-signal"/"rebalance" edge transitions (e.g. new BUY signal while IN_POSITION loops back to SIGNALLED).
- Notable: comments document real trading-desk edge cases: holiday-gap re-signal, order-intent re-submission superseding a pending batch, exit-only path (positions from a prior cycle, no new signal). Raises on invalid transitions, same fail-closed posture as the order FSM.

### src/autonomous_trading_platform/execution/services/trading_freeze_service.py (13 lines)
- Purpose: ostensibly a trading-freeze mechanism.
- Notable: SMELL — this is a stub/placeholder: `freeze_trading()` only `print()`s a message (not even using the structured `logger` used everywhere else in this package) and does not persist any freeze state; `is_trading_frozen()` unconditionally `return False`. If anything in the runtime actually depends on this service to halt trading, it currently cannot do so — any claim of a functioning "trading freeze" safety mechanism routed through this class would be false. Should be cross-checked against `safety/` kill-switch services, which appear to be the real enforcement point elsewhere in the codebase.

### src/autonomous_trading_platform/execution/services/volatility_scaling_config.py (39 lines)
- Purpose: config dataclass toggling volatility-aware sizing enforcement strictness (`require_vol_scalar_for_live`, `allow_full_size_when_scalars_missing`, audit-notional floor).
- Notable: docstring is explicit and honest about the risk being mitigated: "the most dangerous silent fallback: a full-size live order placed during a high-vol regime because bar data was temporarily unavailable" — good articulation of a real failure mode being defended against.

### src/autonomous_trading_platform/execution/services/volatility_scaling_service.py (90 lines)
- Purpose: computes `vol_scalar = min(target_annual_vol / realized_annual_vol, 1.0)` from recent bar closes (annualized stddev of log returns); returns `None` (skip scaling) when fewer than `min_bars` (default 20) bars are available.
- Notable: VERIFIES volatility-based dynamic position scaling — textbook vol-targeting formula, correctly capped at 1.0 (only scales down). Same annualization/log-return convention as `SharpeScalingService`, designed to compose multiplicatively (documented in both docstrings).

## risk/ (7 files)

### src/autonomous_trading_platform/risk/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/risk/risk_engine.py (175 lines)
- Purpose: pure-computation `RiskEngine.compute_exposures()` builds an immutable `PortfolioExposureSnapshot` (gross/net/long/short totals, per-strategy `StrategyExposure`, per-sector `SectorExposure`, max single-strategy weight, and strategy-level Herfindahl-Hirschman Index) from raw `{symbol: qty}` positions + prices + optional strategy/sector maps.
- Notable: no I/O, no side effects — designed to be called once per cycle and fanned out to `RiskAlertService`/`RebalancingService`. Missing-price symbols are skipped with a warning rather than raising or defaulting to zero-notional silently mis-sizing exposure. HHI is a genuine concentration metric (sum of squared weights), not just max-weight — a step beyond the minimum bar for "concentration checks."

### src/autonomous_trading_platform/risk/correlation_service.py (203 lines)
- Purpose: `compute_pairwise_correlations`/`average_pairwise_correlation` (Pearson, via numpy, series trimmed to shortest common length) plus `correlation_penalty_scalar` (linear taper in `[min_scalar, 1.0]` once max abs pairwise correlation exceeds a threshold, default 0.7/0.5) and `CorrelationAwareAllocationProvider`, a decorator over any `IAllocationProvider` that down-scales `allocated_capital_usd` for highly-correlated strategies.
- Notable: decorator pattern is correctly one-directional — explicitly documented and enforced (`if scalar >= ONE: return base`) to only ever reduce, never increase, the wrapped provider's allocation; all non-capital `AllocationResult` fields pass through unchanged. This is real cross-strategy diversification risk control, not a stub — but note it is a decorator that must be explicitly wired around a base provider; nothing in the read scope proves it is actually composed into the live/paper allocation path (would need to check callers, e.g. `RiskContext` construction site, out of this file's scope).

### src/autonomous_trading_platform/risk/portfolio_vol_targeting_service.py (102 lines)
- Purpose: `PortfolioVolTargetingService.compute_scalar()` — portfolio-level (not per-symbol) vol-targeting scalar in `(0,1]` from the equity curve: `min(target_annual_vol/realized_annual_vol, 1.0)`, annualized via sqrt(252 trading days).
- Notable: explicit docstring distinguishing this from the per-asset `execution/services/volatility_scaling_service.py` (equity-curve input vs bar-closes input, one scalar for the whole book vs per-symbol, different annualization base) — a deliberate, documented second layer of vol control at the portfolio level, not a duplicate. `compute_realized_vol` is exposed separately for `RiskAlertService`'s vol-breach check to reuse the same computation rather than reimplementing it.

### src/autonomous_trading_platform/risk/rebalancing_service.py (145 lines)
- Purpose: `RebalancingService.compute_rebalance()` compares current per-strategy weights (derived from a `PortfolioExposureSnapshot`) against configured `target_weights`, emitting sorted `RebalanceDelta` entries (reduce/increase + suggested target capital) for any strategy whose drift exceeds `drift_threshold` (default 5%).
- Notable: explicitly advisory only — docstring states acting on deltas (placing orders) is the caller's responsibility; this file does not touch orders/execution itself. Constructor validates `target_weights` sum <= 1.0 and all non-negative at construction time (fail-fast on bad config) rather than at call time.

### src/autonomous_trading_platform/risk/risk_alert_service.py (365 lines)
- Purpose: `RiskAlertService.evaluate()` runs a `PortfolioExposureSnapshot` (plus optional equity curve / realized vol / avg correlation) through six independent WARNING/CRITICAL threshold checks (gross exposure ratio, strategy concentration, sector concentration, drawdown, portfolio vol, avg pairwise correlation) via `RiskThresholds`, and dispatches `RiskAlert`s through pluggable `IRiskNotifier`s (default: structured logging).
- Notable: VERIFIES sector/strategy concentration checking exists at the alerting layer (complementing `risk_snapshot_service.py` in execution/, which computes utilization but doesn't itself gate on sector concentration — this file is the actual sector-concentration alerting logic, cross-referencing the earlier open question from that entry). All six checks are independent and additive (a single `evaluate()` call can fire multiple alerts simultaneously) rather than short-circuiting on the first breach — correct for an alerting system where operators need the full picture. Drawdown check explicitly documents keeping its running-peak calculation (`np.maximum.accumulate`) numerically consistent with the separate `risk_metrics.max_drawdown` implementation used elsewhere, to avoid silent divergence between the two. Caveat: `evaluate()` is pure alerting/observability — it does NOT block trades itself (no exceptions raised, no gate returned); enforcement of these thresholds as hard pre-trade blocks would need to live elsewhere (e.g. safety/ or the portfolio_drawdown_governance_service in execution/, which is a separate hard-gate mechanism from this soft-alert one).

### src/autonomous_trading_platform/risk/risk_context.py (25 lines)
- Purpose: plain dataclass bundling `RiskEngine` + `RiskAlertService` + `PortfolioVolTargetingService` + `RebalancingService` + optional `CorrelationAwareAllocationProvider` for composition-root wiring/handoff, mirroring the `ExecutionContext` pattern in execution/contexts/.
- Notable: `correlation_aware_provider` is optional (`| None = None`) — consistent with the correlation service note above that this decorator is opt-in and its actual wiring into a live call path is not proven within risk/ itself.

## portfolio/ (6 files)

### src/autonomous_trading_platform/portfolio/__init__.py (0 lines)
- Purpose: empty package marker.

### src/autonomous_trading_platform/portfolio/allocation_provider.py (31 lines)
- Purpose: `IAllocationProvider` runtime-checkable Protocol — `get_allocation`, `total_capital` property, `update_total_capital`. Two implementations satisfy it: `PortfolioEngine` (live/paper, DB-backed) and `SimulationAllocationProvider` (backtest, static config) — plus `CorrelationAwareAllocationProvider` in risk/ as a decorator.
- Notable: clean seam allowing backtest and live paths to share the exact same downstream sizing code (`PositionSizer`, `PortfolioConstructionService` in execution/) while swapping only the capital-resolution strategy.

### src/autonomous_trading_platform/portfolio/exceptions.py (149 lines)
- Purpose: `PortfolioError` hierarchy — `AllocationDeniedError`, `NoPolicyFoundError`, `StrategyNotFoundError`, `InsufficientCapitalError`, `StaleCapitalDataError`, `MissingCapitalDataError`, `MissingPositionScalingDataError`, `AllocationBudgetExceededError`, each carrying structured context fields (not just a message string) for programmatic handling upstream.
- Notable: `StaleCapitalDataError`/`MissingCapitalDataError` explicitly document a fail-closed doctrine — "capital sizing cannot proceed with stale equity data in live/paper modes" — matching the pattern seen in execution/'s volatility scaling config. `MissingPositionScalingDataError` is the exact exception referenced (and raised) in `execution/services/portfolio_construction_service.py`'s fail-closed live-mode volatility-scalar gate, confirming that claim end-to-end from the exception's defining module.

### src/autonomous_trading_platform/portfolio/models.py (75 lines)
- Purpose: Pydantic contracts `AllocationResult` (capital + freshness metadata + drawdown-scaling context fields, tagged `FINDING-13`/`FINDING-12` in comments — suggests these fields were added in response to a prior audit), `PromotionEligibility`, `CriterionResult`.
- Notable: `AllocationResult` carries both capital-freshness provenance (`cash_snapshot_id`, `cash_snapshot_as_of`, `snapshot_age_seconds`, `capital_source`) and drawdown-scaling provenance (`realized_drawdown`, `drawdown_utilization`, `drawdown_scalar`, `drawdown_scaling_applied`) as optional fields defaulting to `None`/`False` — a genuinely auditable data shape, not a bare capital number. The `FINDING-N` comment tags are worth flagging as evidence of iterative audit-driven hardening already having occurred on this exact file.

### src/autonomous_trading_platform/portfolio/portfolio_engine.py (365 lines)
- Purpose: `PortfolioEngine` is the live/paper `IAllocationProvider` implementation: resolves `get_allocation()` via governance-state gate (`_ALLOCATABLE_STATES` = APPROVED_RESEARCH/APPROVED_PAPER/APPROVED_LIVE only — PROPOSED/REJECTED/RETIRED blocked) -> DB policy lookup -> active manual override merge (override fields win where non-None) -> `max_pct_of_capital * total_capital`; also implements `check_promotion_eligibility()` (Sharpe/drawdown/days-tested/trade-count/CAGR/win-rate criteria against `PromotionRulesRepository`) and `get_aggregate_allocation_pct()` for pre-persistence validation of proposed overrides against a total-allocation budget.
- Notable: `get_aggregate_allocation_pct` explicitly supports `proposed_overrides` — i.e., it can answer "what would total allocation be IF this override were applied" before writing it, which is exactly the check `AllocationBudgetExceededError` implies exists somewhere; the actual raise-site for that exception is not in this file (likely the route/service that calls this method with the proposed override and compares against a configured max — worth cross-checking the settings/controls route, out of assigned scope). `get_allocations_for_many` and `get_aggregate_allocation_pct` both silently skip/continue past `AllocationDeniedError`/`NoPolicyFoundError` for individual strategies rather than failing the whole batch — reasonable for a portfolio-wide summary but means one strategy's misconfiguration is invisible unless logged elsewhere (no logging call in the skip branches themselves).

### src/autonomous_trading_platform/portfolio/simulation_allocation_provider.py (393 lines)
- Purpose: `SimulationAllocationProvider` — the backtest-path `IAllocationProvider`, holding a frozen `AllocationConfig` snapshot (per-strategy allocation entries + defaults) captured once via `snapshot_allocation_config()` before a simulation run begins, guaranteeing deterministic/reproducible allocation regardless of subsequent production policy changes.
- Notable: `AllocationConfig.allocation_config_hash` is a genuine deterministic SHA-256-based fingerprint (`_compute_allocation_hash`, 16 hex chars, `json.dumps(..., sort_keys=True)` over behavior-affecting fields only, excluding timestamp/captured_by metadata) included in simulation artifacts/manifests for lineage tracking — same idempotent-hashing quality bar as the execution-layer client_order_id schemes, applied here to reproducibility rather than dedup. `snapshot_allocation_config()` docstring is explicit that it must be called exactly ONCE at simulation start, before the bar loop, and never again during execution — a documented invariant, though nothing in this file enforces that at runtime (a caller could misuse it by calling it mid-loop; enforcement would have to be by convention/code review, not a guard here). Simulation path deliberately does NOT enforce the production governance-approval gate (`_ALLOCATABLE_STATES` in `PortfolioEngine`) — any strategy_id including a StubStrategy may allocate — a documented and correct simulation/live behavioral divergence, not an oversight.

## Standout candidates
- `execution/policy/twap_slicer.py` + `vwap_lite_slicer.py` + `slippage_calculator.py` + `execution_policy_engine.py`: real, non-stub TWAP/VWAP-lite slicing and slippage/cost modeling, with an honest architectural caveat that live-mode slicing is a schedule embedded in metadata, not actual child-order routing.
- `safety/services/order_idempotency_service.py` (cross-checked from execution/services/order_execution_service.py): genuine SHA-256-keyed idempotency check over `run_id|strategy_id|bar_timestamp|symbol|side|qty` within a configurable time window.
- `execution/services/order_state_machine_service.py` + `strategy_state_machine_service.py`: two independent, explicit, audit-logged state machines with hard-fail-on-invalid-transition semantics.
- `execution/services/broker_order_mapper.py` (`extract_incremental_fill`) reused identically by both the polling (`broker_runtime_sync_service.py`) and streaming (`broker_stream_fill_processor.py`) fill paths — a single, shared, monotonic delta-based duplicate-fill/regression-detection mechanism, reinforced by an independent second guard in `order_runtime_state_service.py`.
- `execution/services/portfolio_drawdown_governance_service.py`: the most heavily documented file in the audited scope — explicit fail-closed/fail-open/idempotent-event design-principles docstring for a hard portfolio-wide drawdown circuit breaker, distinct from and composing with the smoother `drawdown_scaling_service.py` per-strategy taper.
- `risk/risk_engine.py` + `risk_alert_service.py`: a full independent risk-monitoring layer (gross/net/sector/strategy-HHI exposure, six threshold-based alert checks) that is purely observational/advisory — worth noting this is a *second*, softer risk layer alongside the hard pre-trade gates presumably in `safety/`.
- `portfolio/simulation_allocation_provider.py`: deterministic SHA-256-hashed `AllocationConfig` snapshotting for reproducible backtests, with documented (if not runtime-enforced) single-snapshot-at-start invariant.

## Gaps/smells
- `execution/services/trading_freeze_service.py` (13 lines): confirmed non-functional stub — `freeze_trading()` only `print()`s, `is_trading_frozen()` unconditionally returns `False`. Any code path relying on this class to actually halt trading cannot do so; real enforcement appears to live in `safety/` kill-switch services (out of assigned scope, not verified here).
- Two distinct, non-unified idempotency-key schemes coexist: `portfolio_construction_service.py`'s deterministic uuid5-based `client_order_id` vs `safety/services/order_idempotency_service.py`'s SHA-256-based dedup check — both real, but a duplication/confusion risk since they're keyed similarly but differently and live in different layers.
- `execution/services/risk_snapshot_service.py`: computes sector/symbol concentration utilization for visibility but does NOT include them in its own `is_blocked`/`block_reasons` gating — full pre-trade concentration gating (if it exists) must live in `safety/services/pre_trade_risk_service.py`, which is out of this scope and was not verified.
- `risk/risk_alert_service.py` is purely observational (logs/notifies) — it does not itself block trades; if the six threshold checks it implements (gross exposure, strategy/sector concentration, drawdown, portfolio vol, correlation) are meant to be enforced as hard pre-trade blocks, that enforcement is not in this file and was not traced further.
- `risk/correlation_service.py`'s `CorrelationAwareAllocationProvider` and `portfolio/allocation_provider.py`'s decorator pattern: real and correctly one-directional, but this audit did not find, within the read scope, a concrete call site proving it is actually wired into the live/paper allocation path rather than being an available-but-unused capability.
- `execution/services/broker_runtime_sync_service.py`: module docstring calls positions/open-orders/fills sync "narrow stubs until integration points are clarified," but the actual methods are fully implemented — stale/misleading docstring, not a functional gap, but worth a doc fix.
- `execution/services/external_broker_reconciliation_service.py`: duplicate-fill detection is an admitted coarse proxy (orders with prior fills absent from the broker's open-order list), not a true per-order comparison — documented limitation, not silent.
- `execution/clients/simulated_broker_client.py`: several broad `except Exception: pass` blocks around DB/Parquet reads — could mask real errors during backtest replay.
- `execution/policy/slippage_calculator.py`: dead conditional block (`_effective_rate`) whose body is `pass` — vestigial code suggesting incomplete cleanup, low severity.
- Zero TODO/FIXME/XXX markers across all three directories (execution/risk/portfolio) — either genuinely clean or markers are tracked elsewhere (e.g. ticket system) rather than inline.

## Coverage: read 61 of 61
No skips. All `.py` files in `execution/` (48), `risk/` (7), `portfolio/` (6) were read in full across this and prior passes — includes 9 zero-byte `__init__.py` package markers, counted individually in the header total but consolidated into shared entries in this file where multiple empty inits share identical purpose. TODO/FIXME/XXX grep across all three directories returned 0 matches.
