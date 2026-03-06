# Autonomous-Trading-Platform

## Development Environment Setup

### Python Version
This project requires Python 3.11.x.

Verify: python --version

### Recommended IDE

PyCharm Professional or Community Edition is recommended.
Ensure the interpreter is set to the project's `.venv`.

---

### 1. Clone Repository
- git clone https://github.com/TristanODonnell/Autonomous-Trading-Platform.git
- cd Autonomous-Trading-Platform

---

### 2. Create Virtual Environment
Windows:
- py -m venv .venv
- .\.venv\Scripts\activate

macOS / Linux:
- python3 -m venv .venv
- source .venv/bin/activate

---

### 3. Install Dependencies
Upgrade pip:
- python -m pip install --upgrade pip

Install pinned runtime dependencies:
- python -m pip install -r requirements.txt

Install the project(src layout, editable mode):
- python -m pip install -e .

---

### 4. Install Dev Tooling
Install development dependencies:
- python -m pip install -r requirements-dev.txt

Install pre-commit hooks:
- pre-commit install

---

### 5. Run Tests
- python -m pytest

---

### 6. Environment Configuration
Copy example config:
Windows:
- copy .env.example .env.dev

MacOS / Linux:
- cp .env.example .env.dev

Populate required variables before running the app.

## Canonical Docs
- docs/v1-boundaries.md
- docs/safety-doctrine.md
- docs/invariants.md
- docs/compile-vs-runtime.md
- docs/contracts/index.md
- docs/decisions.md

- docs/storage/index.md
- docs/storage/postgres-system-of-record.md
- docs/storage/parquet-datasets.md
- docs/storage/dataset-versioning.md
- docs/storage/universe-versioning.md
- docs/storage/audit-log.md

## Status

Current Phase: Phase 1 — Data Model & Contract Implementation
Mode: Active Implementation

Baseline assumptions:
- 5-minute bars
- Alpaca market data + brokerage
- Single strategy (v1 vertical slice)
- Single universe
- Single capital bucket
- Paper trading only (NO_LIVE_TRADING enforced)

The system is transitioning from architecture specification to concrete
implementation beginning with the canonical data contract layer.

### Phase 1 Complete
The following are now locked and versioned:

- Canonical contract definitions (MarketBar → RunManifest)
- Planning-level invariants (alignment, idempotency, capital safety)
- Reproducibility guarantees (dataset pinning, config snapshot, commit hash)
- Risk enforcement boundary defined
- Execution lifecycle defined

### Phase 2 Complete
The following are now locked and versioned:

- Postgres system-of-record plan (runs, manifest, orders/fills/events, snapshots)
- Parquet dataset layout conventions (raw vs adjusted bars, corporate actions)
- DatasetVersion contract and lineage requirements (checksums, schema versioning, coverage windows)
- UniverseVersion rules (snapshot semantics, membership hash, selection criteria)
- Minimal immutable audit log requirements (run/step lifecycle, order transitions, reconciliation outcomes)

### Phase 3 Complete
The following are now locked and versioned:

- v1 Universe specification (eligibility filters, exclusions, cadence)
- Deterministic UniverseSnapshot semantics
- Monthly rebalance governance
- Symbol lifecycle mapping (delisting, merger, rename handling)
- Survivorship bias elimination rules
- Eligibility reconstruction guarantee:
  “Was this symbol tradable on this date?”

Universe membership is now time-aware, versioned, and reproducible.

### Phase 4 Complete
The following are now locked and versioned:

- Environment isolation model (paper vs live separation)
- Paper-only build path for v1
- Multi-layer live enablement gates:
  - Build-time gate
  - Config gate
  - Runtime human-confirmed activation token
  - External kill-switch outside DB + service process
- Hard caps and throttles (exposure, notional, rate limits)
- Shadow mode (logic runs, broker disabled)
- Broker account allowlist enforcement
- Deterministic idempotency + duplicate prevention policy

Capital-protection architecture is now formally defined.

The system is provably live-proofed at the design layer.

### Phase 5 Complete
The following are now locked and versioned:

- Deterministic 5-minute scheduler model (bar close → ingestion SLA → evaluation start)
- Explicit skip / degrade / halt decision tree for SLA misses
- Runtime cycle event sequence (BAR_CLOSED → CYCLE_COMPLETED)
- Order execution state machine:
  - Defined transition graph
  - Terminal-state immutability
  - Forbidden transitions
  - Retry policy (network-only)
  - Trigger → event mapping
- Strategy lifecycle state machine:
  - IDLE → SIGNALLED → PENDING → IN_POSITION → EXIT_PENDING → COOLDOWN
  - Position ownership invariants
  - Forbidden transitions
  - Trigger → event mapping
- Reconciliation model:
  - Runs every evaluation cycle + end-of-day
  - Mismatch → freeze + alert + human acknowledgment required
- Global runtime event contract:
  - Every state transition must emit immutable event
  - No ambiguous execution states permitted

Runtime behavior is now fully deterministic at the design layer.
Execution implementation must conform to the locked scheduler and FSM semantics.


### Phase 6 Complete
The following ingestion semantics are now locked and versioned:

- v1 ingestion sources (Alpaca free feeds, 5-minute bars)
- Raw vs adjusted storage policy (split-adjusted series + dividend events)
- Deterministic 5-minute data SLA model:
  - Freshness window (bar_close + 30s)
  - Hard deadline (bar_close + 90s)
- Explicit breach actions:
  - SKIP (symbol-level)
  - DEGRADE (safe mode)
  - HALT (cycle-level)
- Outlier detection thresholds and rejection rules
- Missing bar behavior:
  - Controlled forward-fill
  - Escalation thresholds
  - No new entries on synthetic data
- Corporate action continuity validation
- Ingestion incident event schema

Ingestion behavior is now fully deterministic at the design layer.


### Phase 7 Complete
The following research engine semantics are now locked and versioned:

- Backtest engine shares canonical contracts with paper/live:
  Signal → OrderIntent → BrokerOrder/Fills (simulated) → Positions → Reports
- Bar-based simulation model with event-like order transitions (v1)
- Deterministic fill timing rule (T close → fill eligibility at T+1)
- Fill + cost model contracts:
  - v1 linear cost model
  - v1 VolumeShare-like slippage model (parameterized + capped)
- Experiment tracking artifacts:
  - run_manifest.json
  - metrics_summary
  - trades journal
  - debug report
- Stress test specification:
  - gaps, volatility spikes
  - data outages / delayed bars
  - extreme slippage
  - partial fills

The research engine is now contract-compatible with live execution and produces
reproducible, audit-friendly outputs.

### Phase 8 Complete
The v1 vertical slice strategy specification is now locked and versioned:

- Single strategy spec (bar-based):
  - Entry/exit logic
  - Holding assumptions
  - Risk constraints (position sizing, max concurrent positions, exposure caps)
- Failure semantics defined:
  - Signal rejected by risk gate → signal recorded, no OrderIntent generated
  - Repeated flip / churn → cooldown + suppression rules
- Execution constraints locked:
  - Order types: market + limit only
  - Extended-hours behavior explicitly defined (default OFF)
  - Stop-loss policy explicitly defined (v1 disabled; replacement controls documented)
- Acceptance criteria:
  - Strategy can be run through the paper pipeline and OrderIntent patterns predicted
  - Strategy behavior under missing/late data is fully defined

The v1 vertical slice is now fully specified at the design layer.

### Phase 9 Complete
The following paper-trading validation gates are now locked and versioned:

- Minimum validation window defined (10 consecutive market days)
- Required validation outcomes:
  - Zero safety gate violations
  - Zero reconciliation mismatches
  - Idempotency proven under restarts
  - Complete audit trail for every lifecycle event
- Operational playbook defined:
  - Incident response protocol
  - Kill switch test cadence
  - Manual intervention policy
  - Broker connectivity monitoring rules
- Promotion standard locked:
  - End-to-end correctness requirement
  - Reproducibility guarantee
  - Audit trace reconstructability
  - Safety controls verified

The system now has defined criteria for calling v1 complete.

# v1 Project Implementation

## Phase 0 — Development Baseline

Phase 0 establishes a reproducible development environment and project scaffolding.

### Introduced

- `src/` project layout
- Environment configuration loader (`config.py`)
- Database engine factory (`db.py`)
- Local Postgres container via Docker Compose
- Pytest test framework with coverage reporting
- Initial tests:
  - Environment loading validation
  - Database connectivity validation
- Pre-commit hooks (ruff, mypy, formatting)
- MkDocs documentation portal

### Result

The repository can now:

- Run linting and type checking locally
- Run tests with coverage
- Connect to a local Postgres system-of-record
- Serve documentation locally via MkDocs

## Phase 1 — Data Model & Contract Implementation

Phase 1 implements the canonical contract layer defined in the architecture
specification. These contracts represent the core data structures used across:

- ingestion
- research/backtesting
- execution
- ledger/accounting
- reporting

All system components must exchange data exclusively through these models.

### Implemented Contracts

- MarketBar
- CorporateAction
- UniverseSnapshot
- Signal
- OrderIntent
- BrokerOrder
- Fill
- PositionSnapshot
- CashSnapshot
- RiskSnapshot
- RunManifest

These models enforce strict type validation and invariant rules.

### Validation System

A reusable validator framework enforces domain invariants such as:

- bar timestamp alignment (5-minute boundaries)
- monotonic time progression
- OHLC sanity checks
- non-negative balances and quantities
- valid order lifecycle transitions

Validation rules can be executed during:

- ingestion
- backtesting
- strategy evaluation
- execution reconciliation

### Storage Implementation

Postgres tables have been created using Alembic migrations to serve as the
system-of-record (SOR) for trading state.

Implemented tables:

- market_bars
- corporate_actions
- universe_snapshots
- signals
- order_intents
- broker_orders
- fills
- position_snapshots
- cash_snapshots
- risk_snapshots
- run_manifests

Database constraints enforce many of the same invariants as the contract layer.

### Parquet Datasets

Market data is stored in Parquet datasets for efficient analytical access.

Datasets include:

- raw market bars
- adjusted market bars
- corporate actions

Partitioning strategy:
