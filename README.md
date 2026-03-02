# Autonomous-Trading-Platform

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
- Current Phase: Phase 4 — Safety Architecture (Live-Proofing)
- Mode: Design / Architecture Only (No Implementation)
- Baseline: 5-minute bars - Alpaca - single strategy - single universe - single capital bucket
- Default: NO_LIVE_TRADING (paper/shadow only)

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

Next: Phase 5 - Scheduler Semantics + State Machines

