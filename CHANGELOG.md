# Changelog

## v0.1.0 — Phase 0 Spec Artifacts Locked

Added:
- v1 boundaries and explicit non-goals
- Safety doctrine (capital protection invariants)
- Compile-time vs runtime semantics
- Canonical contracts index (v1)
- Global invariants list
- Decision log scaffold

Notes:
This release locks the non-negotiable architecture and safety constraints for v1 before implementation begins.

## v0.2.0 — Phase 1 Canonical Contracts Locked

Added:
- Full canonical contract definitions:
  - MarketBar
  - CorporateAction
  - UniverseSnapshot
  - Signal
  - OrderIntent (idempotency guarantees)
  - BrokerOrder (lifecycle state machine)
  - Fill (execution truth)
  - PositionSnapshot
  - CashSnapshot
  - RiskSnapshot
  - RunManifest (reproducibility root)
- Formal invariants for:
  - Bar alignment and monotonicity
  - Corporate action continuity
  - Universe time-awareness (no survivorship leakage)
  - Order idempotency
  - Capital safety
  - Risk enforcement
- Reproducibility constraints:
  - Dataset and universe version pinning
  - Strategy config snapshot
  - Git commit pinning
  - Conditional backtest determinism rules

Notes:
This release freezes the canonical contract layer before implementation begins.
All execution and ledger behavior must conform to these contracts.

## v0.3.0 — Phase 2 System-of-Record + Versioning Locked

Added:
- Storage layer index (Phase 2)
- Postgres system-of-record planning spec (tables + invariants)
- Parquet dataset conventions (raw/adjusted, partitions, version folders)
- Dataset versioning + lineage rules (checksums, schema version, coverage windows)
- Universe versioning rules (membership hashing, selection criteria, snapshot semantics)
- Minimal immutable audit log requirements (run/step, order lifecycle, reconciliation)

Notes:
This release locks storage and lineage semantics required for reproducible replays and auditability.
All future implementation must reference RunManifest → DatasetVersion/UniverseVersion → immutable storage artifacts.

## v0.4.0 — Phase 3 Universe Governance + Survivorship Controls Locked

Added:
- v1 Universe specification:
  - Universe ID and data source (Alpaca IEX)
  - Eligibility filters (price floor, liquidity threshold)
  - Explicit asset exclusions (ETFs, ADRs, OTC, SPACs)
  - Locked rebalance cadence (monthly)
- Formal Universe lifecycle handling:
  - Delisting behavior
  - Merger and cash-out semantics
  - Symbol change mapping rules
  - Non-retroactive membership guarantees
- Survivorship bias controls:
  - Time-aware UniverseSnapshot enforcement
  - Historical membership reconstruction rules
  - Deterministic eligibility resolution:
    “Was symbol X eligible on date Y?”
- Universe invariants:
  - Immutable snapshot storage
  - Membership hashing
  - UniverseVersion reproducibility guarantees

Notes:
This release freezes universe selection semantics and survivorship controls.
All historical runs must reference a versioned UniverseSnapshot.
No dynamic or future-aware membership is permitted.

## v0.5.0 — Phase 4 Safety Architecture (Live-Proofing) Locked

Added:
- Formal environment isolation model:
  - Strict separation of paper vs live namespaces
  - Environment-scoped credentials and account bindings
  - Paper-only build path (live code excluded by default)
- Layered enablement gates (multi-layer live protection):
  - Build-time gate
  - Configuration gate (NO_LIVE_TRADING default)
  - Runtime human-confirmed live activation token
  - External out-of-band kill switch (outside DB + main service)
- Hard caps & throttles:
  - Gross exposure limits
  - Per-symbol exposure limits
  - Daily notional limits
  - Order rate limits (per bar / per hour)
- Shadow mode specification:
  - Full decision-layer execution
  - Zero broker initialization
  - No network calls permitted
- Broker account allowlist enforcement:
  - Environment-scoped account_id allowlists
  - Pre-execution validation
  - Allowlist defined outside primary database
- Deterministic idempotency key strategy and duplicate prevention policy

Notes:
This release locks capital-protection architecture before execution implementation begins.

Live trading now requires:
- Explicit build enablement
- Explicit config override
- Runtime activation token
- External kill-switch inactive
- Account allowlist validation
- Cap validation
- Idempotency validation

No single bug can route paper orders to live.

## v0.6.0 — Phase 5 Scheduler Semantics + Runtime State Machines Locked

Added:
- Deterministic 5-minute scheduler model:
  - Canonical bar-close semantics (UTC)
  - Ingestion SLA deadlines
  - Evaluation start guarantees
  - Explicit skip / degrade / halt decision tree
- Formal runtime cycle event sequence:
  - BAR_CLOSED
  - INGESTION_SLA_PASSED / MISSED
  - RECONCILIATION_STARTED / PASSED / FAILED
  - EVALUATION_STARTED / COMPLETED
  - EXECUTION_WINDOW_STARTED / COMPLETED
  - CYCLE_COMPLETED
- Order state machine (execution-layer FSM):
  - Explicit transition graph
  - Terminal-state immutability
  - Forbidden transitions defined
  - Retry policy (network-only)
  - Trigger → recorded event mapping
- Strategy lifecycle state machine:
  - IDLE → SIGNALLED → PENDING → IN_POSITION → EXIT_PENDING → COOLDOWN
  - Position ownership invariants
  - Explicit forbidden transitions
  - Trigger → recorded event mapping
- Reconciliation schedule formalized:
  - Runs every evaluation cycle
  - Mandatory end-of-day reconciliation
  - Restart reconciliation requirement
- Mismatch enforcement policy:
  - Immediate freeze
  - Order cancellation
  - CRITICAL alert emission
  - Human acknowledgment required before resume
- Global event-recording contract:
  - Every transition must emit immutable event
  - No ambiguous “maybe filled” state allowed
  - No implicit transitions permitted

Notes:
This release freezes runtime lifecycle semantics prior to execution implementation.

All future engine behavior must conform to the defined scheduler model,
state machines, reconciliation enforcement, and event-recording guarantees.


## v0.7.0 — Phase 6 Ingestion Pipeline Semantics Locked

Added:
- Formal v1 ingestion source specification:
  - Alpaca free feeds (IEX + delayed SIP where available)
  - 5-minute bar ingestion model
  - Corporate action ingestion requirements
- Raw vs adjusted storage policy:
  - Raw provider-native bars stored
  - Adjusted bars derived via split adjustment_factor
  - Dividend handling recorded as events (no dividend-adjusted series in v1)
- Per-cycle Data SLAs defined:
  - Freshness target (bar_close + 30s)
  - Absolute lateness tolerance (bar_close + 90s hard deadline)
- Deterministic breach decision tree:
  - SKIP (symbol-level)
  - DEGRADE (safe mode, no new entries)
  - HALT (cycle-level)
- Outlier detection thresholds:
  - Extreme price deviation guardrails
  - Range sanity checks
  - Stale/zero-volume detection
- Missing data policy locked:
  - Forward-fill limited to indicator continuity only
  - No new entries on forward-filled data
  - Escalation thresholds for multi-bar absence
- Corporate action continuity checks:
  - Split-adjusted price continuity tolerance
  - Adjustment-factor validation rules
- Ingestion incident event contract:
  - INGESTION_SLA_PASSED / MISSED
  - MARKETBAR_MISSING / OUTLIER / INVALID
  - CORPORATE_ACTION_CONTINUITY_BREACH
  - Explicit action_taken field (SKIP / DEGRADE / HALT)

Acceptance:
For any given bar timestamp T, the system can deterministically state:
- When it must be available
- What action is taken if it is not
- What immutable event is recorded

Notes:
This release freezes ingestion behavior prior to execution implementation.
All future runtime logic must rely on these ingestion guarantees.

## v0.8.0 — Phase 7 Research Engine (Backtest + Experiment Tracking) Locked

Added:
- Research engine specification sharing live contracts:
  - Signal → OrderIntent → BrokerOrder/Fills (simulated) → Positions → Reports
  - No research-only schemas permitted
- Backtest semantics (v1):
  - Bar-based simulation with event-like order transitions
  - Fill timing rule locked (orders generated at bar close T, eligible to fill at T+1)
  - Deterministic partial-fill behavior and cancellation rules
- Fill + cost model contracts (v1):
  - Linear cost model (commission/spread/slippage parameters)
  - VolumeShare-like slippage model (parameterized, capped)
  - Deterministic fill rules for market + limit orders
- Experiment tracking outputs (required artifacts):
  - run_manifest.json (reproducibility root)
  - metrics_summary.json
  - trades_journal (CSV/Parquet)
  - debug_report.json (incidents + stress injections)
- Stress test specification (required cases):
  - gap shocks / volatility spikes
  - data outages / delayed bars
  - extreme slippage
  - partial fill scenarios

Acceptance:
The backtest engine produces the same output “shape” as paper trading:
Signal → OrderIntent → BrokerOrder/Fills (simulated) → Positions → Reports

Notes:
This release freezes the research engine semantics so the execution engine
(paper/live) can reuse identical contracts and reporting surfaces.