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
- Current Phase: Phase 2 — System-of-Record Design (Postgres + Parquet) + Versioning
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

Next: Phase 3 — Execution/Orchestration Vertical Slice Design (bar loop + state machines + broker adapter boundaries)