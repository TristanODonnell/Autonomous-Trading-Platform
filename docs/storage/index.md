# Storage Layer (Phase 2) — Index

## Scope

This folder defines the system-of-record storage layout, dataset storage conventions, versioning semantics, and audit trail requirements.

This is planning-level architecture (no implementation). It exists to ensure runs are reproducible and auditable: given a RunManifest, the platform can unambiguously locate the exact datasets, universe membership, configuration, and order outcomes required to re-run an identical simulation later. :contentReference[oaicite:2]{index=2}

## Storage Roles

### Postgres: System of Record (SoR)
Postgres is the system of record for:
- run identity and RunManifest anchoring
- orders, broker events, fills
- periodic state snapshots (positions/cash/risk)
- dataset and universe version registries (metadata + lineage)
- audit events (append-only timeline)

### Parquet: Immutable Historical Datasets
Parquet (object storage) is used for immutable datasets required for:
- historical bars (raw + adjusted)
- corporate actions
- universe membership artifacts (if stored as parquet)
- offline scenario analysis and deterministic replay

These roles align with the platform’s design philosophy around reproducibility and auditability. :contentReference[oaicite:3]{index=3}

## Documents in This Folder

1. `postgres-system-of-record.md`
   - Planning-level table inventory and invariants for Postgres SoR.
   - Defines what must be queryable and auditable.

2. `parquet-datasets.md`
   - Parquet dataset families (raw vs adjusted bars, corporate actions).
   - Partition scheme and version folder conventions.

3. `dataset-versioning.md`
   - How dataset versions are created, named, stored, checksummed, and linked via lineage.
   - Operational rules for producing new DatasetVersions.

4. `universe-versioning.md`
   - How universe snapshots/versions are produced, hashed, stored, and referenced.
   - Rules that prevent survivorship and look-ahead bias.

5. `audit-log.md`
   - Minimal v1 immutable audit trail: run/step lifecycle, order transitions, reconciliation results.

## Interfaces to Contracts

Storage specs reference canonical contracts in `docs/contracts/`:
- `contracts/run-manifest.md` (reproducibility anchor)
- `contracts/order-intent.md`
- `contracts/broker-order.md` / `contracts/fill.md`
- `contracts/position-snapshot.md`, `contracts/cash-snapshot.md`, `contracts/risk-snapshot.md`
- `contracts/universe-snapshot.md` (and UniverseVersion if separated)
- `contracts/corporate-action.md`
- `contracts/marketbar.md`

Storage documents define persistence and lookup rules; contracts define schema and invariants.

## Phase 2 Acceptance Criteria (Definition of Done)

Given a `RunManifest`, the platform can unambiguously locate:

### Data Inputs
- exact market bars dataset version (raw and/or adjusted)
- exact corporate actions dataset version
- exact universe membership (versioned snapshot + membership hash)
- all relevant coverage windows and schema versions
- all checksums required to validate integrity

### Decision Outputs
- all generated OrderIntents (including idempotency keys)
- all internal orders derived from intents

### Broker Outcomes
- full broker order lifecycle (append-only events)
- terminal order states (filled/canceled/rejected)
- all fills (executions)

### State and Risk
- position snapshots, cash snapshots, and risk snapshots at evaluation boundaries
- reconciliation results for each cycle (including mismatches and freeze actions)

### Auditability
- immutable timeline of run/step lifecycle events
- order lifecycle transitions and broker events
- reconciliation events and outcomes

If these criteria hold, the system supports “re-run exact simulation later” as a first-class invariant. :contentReference[oaicite:4]{index=4}