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
- docs/README.md
- docs/architecture/system-overview.md
- docs/architecture/layering.md
- docs/architecture/data-flow.md
- docs/backend/
- docs/operations/
- docs/audits/

## Status

Current Phase: Phase 5 Safety System & Risk Controls
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
- dataset/
- symbol=XYZ/
- date=YYYY-MM-DD/
- part-*.parquet


Each dataset includes metadata fields:

- schema_version
- data_version

### Run Manifest

Every research or trading run produces a RunManifest capturing:

- git commit
- dataset versions
- universe version
- strategy configuration
- cost/fill model versions
- random seed
- environment metadata

This ensures deterministic replay of historical runs.

## Phase 2 — Storage Layer Implementation

Phase 2 implements the storage architecture defined in the system design documentation.

This layer provides deterministic persistence and retrieval for all datasets and trading state.

---

### Repository Layer

Repository classes have been introduced to encapsulate database access for all system-of-record tables.

Repositories provide structured interfaces for:

- insert operations
- updates
- selects
- deletes
- idempotent upserts based on deterministic identifiers

These repositories operate within a **UnitOfWork transactional boundary** to ensure atomic writes across related records.

Example use cases include:

- inserting an order and its fills
- writing ingestion results across multiple tables
- updating run state and audit records together

---

### Versioned Parquet Datasets

Market data and corporate actions are stored in **versioned Parquet datasets**.

Dataset structure:
- dataset_root/
- bars/
- {data_version}/
- symbol=XYZ/
- date=YYYY-MM-DD/
- part-*.parquet


Each dataset version contains metadata describing:

- schema_version
- dataset_name
- data_version
- ingestion timestamp

Reader utilities allow loading datasets using:

- dataset version
- symbol
- date range

This ensures historical runs can always access the **exact dataset version used during execution**.

---

### Universe Snapshot Versioning

Universe membership is stored as versioned snapshots.

Each snapshot records:

- snapshot_date
- list of symbols
- eligibility criteria
- version identifier

Utility services allow deterministic queries such as:
- was_symbol_eligible(symbol, date)


This ensures:

- survivorship bias is eliminated
- historical runs use the correct universe
- delistings and ticker changes do not corrupt backtests

---

### Audit Logging

An immutable **audit log** table captures key lifecycle events across the system.

Recorded events include:

- run lifecycle transitions
- configuration changes
- order state transitions
- reconciliation outcomes
- operational incidents

These logs provide the foundation for monitoring, debugging, and regulatory traceability.

---

### Data Integrity

The storage layer is designed to support checksum validation for Parquet datasets and row-level integrity checks in Postgres.

Checksum verification during dataset reads is planned for a future update.

## Phase 3 — Data Ingestion Pipeline Implementation

Phase 3 implements the ingestion architecture defined in the system design documentation.
This phase introduces the operational pipeline responsible for collecting market data and corporate
actions, validating incoming data, enforcing SLAs, and recording operational incidents.

---

### Market Data Ingestion

The system now ingests **5-minute market bars** using Alpaca’s market data APIs.

Key behaviors:

- Alpaca SDK adapter for provider data access
- Minute bars aggregated into **aligned 5-minute bars**
- All timestamps normalized to **UTC**
- Deterministic bar boundary alignment enforced

Bars are tagged with **market session identifiers**:

- `PRE_MARKET`
- `REGULAR`
- `POST_MARKET`
- `OVERNIGHT`

Extended-hours trading is supported at the data layer, while execution restrictions remain enforced
by the trading subsystem.

---

### Corporate Actions Ingestion

Corporate action events are ingested from Alpaca free data feeds.

Supported event types include:

- Dividends
- Stock splits
- Mergers
- Symbol/name changes

Events are parsed into the canonical `CorporateAction` contract and stored in the system-of-record.

Corporate actions are used to maintain **price continuity across adjusted datasets**:

- Raw bars remain provider-native
- Adjusted bars apply split adjustment factors
- Dividend events are recorded separately

---

### Data Validation & Quality Flags

Incoming market data passes through a validation layer before persistence.

Validation checks include:

- Bar timestamp alignment to 5-minute boundaries
- OHLC sanity checks
- Non-negative volume validation
- Monotonic timestamp enforcement
- Corporate action continuity validation

Bars that violate validation rules are not silently discarded.
Instead they are flagged using structured **quality flags** such as:

- `BAR_MISSING`
- `BAR_OUTLIER`
- `BAR_INVALID`
- `BAR_LATE`

This preserves full auditability of data anomalies.

---

### Outlier Detection & Missing Data Policy

Statistical safeguards are applied to identify abnormal price movements.

Detection methods include:

- log-return deviation thresholds
- range sanity checks
- stale or zero-volume detection

Outliers are **flagged rather than deleted** to preserve raw provider truth.

Missing data behavior follows a deterministic policy:

- Missing bars are recorded as incidents
- Symbol evaluation is skipped for that cycle
- Prices are **never synthesized**
- If many symbols are missing, the cycle may escalate to a **HALT incident**

This guarantees that strategy logic never executes on fabricated market data.

---

### Scheduler & Ingestion SLAs

The ingestion pipeline is orchestrated through **Airflow DAGs**.

Implemented jobs include:

- `run_market_ingestion_cycle`
- `run_market_backfill_cycle`
- `run_corporate_action_ingestion_cycle`

Market data ingestion runs **every 5 minutes** with defined SLAs:

| SLA Stage | Target |
|-----------|--------|
| Freshness Window | bar_close + 30 seconds |
| Hard Deadline | bar_close + 90 seconds |

If the ingestion job breaches its SLA:

- incidents are recorded
- fallback logic may trigger
- the evaluation cycle may skip or halt depending on severity

A **daily backfill pipeline** is also implemented for dataset bootstrap and historical recovery.

---

### Monitoring & Incident Recording

Operational monitoring is implemented through the **audit logging system**.

Each ingestion pipeline records:

- run start / end timestamps
- success or failure state
- ingestion incidents
- SLA breaches
- validation anomalies

Examples of recorded incidents include:

- `INGESTION_SLA_MISSED`
- `MARKETBAR_MISSING`
- `MARKETBAR_OUTLIER`
- `CORPORATE_ACTION_CONTINUITY_BREACH`

Each pipeline execution also generates a **RunManifest** capturing:

- environment metadata
- dataset versions
- universe version
- runtime configuration
- git commit hash

This provides deterministic traceability for every ingestion run.

---

### Result

The ingestion layer now provides:

- deterministic 5-minute market data collection
- corporate action continuity handling
- strict validation and anomaly detection
- SLA enforcement and incident tracking
- fully auditable pipeline runs

This completes the **operational data ingestion foundation required for trading execution and research replay.**

## Phase 4 — Universe Governance Implementation

Phase 4 implements the universe governance layer responsible for selecting,
maintaining, and versioning the trading universe used by the system.

This layer ensures that universe membership is deterministic, reproducible,
and free of survivorship bias during historical analysis.

---

### Universe Selection Script

A universe selection pipeline has been implemented to determine the eligible
trading symbols for the system.

Selection logic:

- Query Alpaca’s available tradable symbols
- Apply deterministic eligibility filters including:
  - minimum price threshold (≥ $1)
  - liquidity thresholds (average daily dollar volume)
  - additional free-data compatibility filters

The resulting symbol set is persisted as a **UniverseSnapshot**.

Each snapshot records:

- snapshot_date
- symbol list
- selection criteria used
- deterministic version identifier

The version identifier is computed using a **hash of the sorted symbol list**
to ensure reproducible universe definitions.

---

### Rebalance Cadence

A universe rebalance job has been introduced:
- run_universe_selection_cycle


This job recalculates the universe at the configured cadence
(daily or weekly depending on configuration).

For each rebalance:

- a new `UniverseSnapshot` is generated
- the snapshot date is recorded
- the version hash is stored
- the selection criteria are persisted

Utility functions allow querying universe membership for **any historical date**.

---

### Ticker Lifecycle Handling

Symbol lifecycle handling has been implemented to ensure continuity when
tickers change due to corporate actions.

Supported lifecycle events include:

- ticker renames
- mergers
- successor symbols
- delistings

Mapping tables and lifecycle utilities allow:

- resolving successor symbols
- maintaining historical membership for delisted tickers
- mapping historical tickers to their current equivalents when required

Historical snapshots always preserve the original symbol that was tradable
at that time.

---

### Survivorship Bias Control

The system enforces survivorship-bias-free historical evaluation.

Backtests and historical queries must reference the **UniverseSnapshot
valid for the evaluation date** rather than the current universe.

Helper services allow:

- filtering market bars using historical universe membership
- filtering corporate actions based on historical eligibility
- resolving valid tradable symbols at any point in time

This guarantees that historical simulations only operate on assets that were
tradable at the time.

---

### Validation & Invariants

Universe snapshot validation has been implemented to enforce governance rules.

Validation checks include:

- every symbol in a UniverseSnapshot must exist in the dataset
- universe membership lists cannot be empty
- snapshot criteria must be recorded
- deterministic version identifiers must be stored

These rules ensure that universe definitions remain reproducible and safe
for both live trading and historical research.

## Phase 5 — Safety System & Risk Controls Implementation

Phase 5 introduces the first concrete implementation of the system's
capital protection architecture. These components enforce strict
risk controls before any order can be submitted to a broker.

---

### Environment Isolation

Trading environments are explicitly separated into:

- `paper`
- `live`

Each environment has:

- independent broker credentials
- environment-scoped account allowlists
- isolated configuration namespaces

The system defaults to **`NO_LIVE_TRADING`**, ensuring that live trading
cannot occur unless explicitly enabled.

---

### Layered Enablement Gates

Multiple independent gates protect against accidental live trading.

Implemented gates:

1. **Build Gate**
   - Paper-only builds exclude live trading modules by default.

2. **Configuration Gate**
   - `enable_live_trading=true` must be explicitly configured.

3. **Runtime Gate**
   - Runtime activation checks required before execution is allowed.

Command-line utilities are provided to verify and toggle these gates.

This layered model ensures that **no single bug can enable live trading**.

---

### Exposure Caps & Risk Limits

A pre-trade risk validation service evaluates every `OrderIntent`
before execution.

Supported controls include:

- Maximum gross exposure
- Per-symbol exposure limits
- Daily notional traded limits
- Order rate limits (per bar or per hour)

Orders violating these limits are rejected before reaching the
execution layer.

---

### Idempotency & Duplicate Protection

To prevent accidental duplicate orders, each `OrderIntent`
generates a deterministic **idempotency key**.

Key inputs:

- run_id
- strategy_id
- bar_timestamp
- symbol
- side
- target_qty

Before creating a broker order, the system checks whether an order
with the same key already exists within a configurable time window.

Duplicate orders are automatically suppressed.

---

### Shadow Mode

Shadow mode allows the trading system to run fully in production
without placing broker orders.

In shadow mode:

- strategy signals are generated
- `OrderIntent` objects are created
- execution logic is simulated
- broker API calls are disabled

This mode allows safe validation of strategy logic and risk controls.

---

### Kill Switch (Planned)

A kill switch service interface has been introduced to support
out-of-band trading halts.

Future implementation will support:

- external kill switch storage (Redis / S3)
- runtime checks by scheduler and strategy engine
- automatic cancellation of open orders when triggered

External kill switch infrastructure is planned for a future phase.

---

### Result

The system now enforces:

- strict environment separation
- multiple live-trading safety gates
- deterministic idempotency guarantees
- pre-trade risk validation
- safe strategy validation via shadow mode

These protections form the **core safety layer required before
implementing broker execution.**
