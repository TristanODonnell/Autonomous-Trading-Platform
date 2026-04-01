# Domain: Storage

## Overview

The storage system provides the persistence layer for all platform data, enabling reproducibility, auditability, and deterministic replay of trading runs.

It consists of:

- Postgres (system of record for operational data)
- Parquet datasets (immutable historical data storage)
- Versioning systems for datasets and universe membership
- Audit logging for event traceability

The intended design ensures that any run can be fully reconstructed from stored data and metadata.

---

## Storage Architecture

The platform uses a hybrid storage model:

- **Postgres (SoR)** → transactional state, run metadata, orders, snapshots
- **Parquet (object storage)** → immutable datasets (market data, corporate actions)

Postgres stores references (metadata + pointers), while Parquet stores bulk historical data.

---

## Postgres System of Record

Postgres stores all operational and transactional data required for execution and replay:

- RunManifest (reproducibility anchor)
- Orders, broker events, and fills
- Position, cash, and risk snapshots
- Universe snapshots
- Dataset version references
- Audit log events

Current behavior:

- ORM models exist for core entities (orders, snapshots, audit logs, etc.)
- Repositories provide insert and upsert operations
- Most entities are linked via run_id

Limitations:

- Many tables allow updates via upsert (not append-only)
- RunManifest immutability is not enforced
- Some schema fields differ from documented contracts

## Parquet Datasets

Parquet is used to store historical datasets required for evaluation and backtesting:

- Raw market bars (bars_raw_5m)
- Adjusted market bars (bars_adj_5m)
- Corporate actions

Current behavior:

- Data is written to partitioned parquet datasets
- Partitioning typically uses symbol and date
- Dataset version is represented by a data_version string

Limitations:

- No strict version=<id> folder convention enforced
- Version folders may be overwritten
- Minimal metadata stored (no full checksum manifest or lineage)
- Dataset coverage windows not tracked


## Versioning (Datasets & Universe)

The intended design uses explicit versioning to ensure reproducibility:

- DatasetVersion → immutable dataset metadata with lineage
- UniverseVersion → deterministic membership snapshot

Current behavior:

- dataset_version is stored as a string in RunManifest
- No DatasetVersion model or registry exists
- Universe snapshots exist but:
  - version is derived from symbol list only
  - universe_id is randomly generated

Limitations:

- No immutable DatasetVersion records
- No lineage tracking between datasets
- Universe versioning is not fully deterministic
- Version identifiers do not encode criteria or timestamps

## Audit Logging

The system includes an audit logging mechanism to track system behavior:

- Events are recorded with:
  - event_id
  - run_id
  - event_type
  - timestamp
  - message / metadata

Current behavior:

- AuditLogEvent model exists
- Events are stored in Postgres
- Used for debugging and tracing execution

Limitations:

- Not append-only (can be modified or overwritten)
- Missing required fields:
  - severity
  - sequence ordering
  - entity references
  - correlation IDs
- No hash chaining or tamper-evidence
- No strict event schema enforcement

## Current Behavior

The storage system provides a working persistence layer:

- Core entities are stored in Postgres
- Parquet datasets store historical data
- Basic version references exist in RunManifest
- Audit logs capture key events

However:

- Many invariants are not enforced
- Versioning is simplified
- Append-only guarantees are not implemented
- Reproducibility relies on conventions rather than strict enforcement

## Limitations

The current storage system is a partial implementation of the intended architecture.

Key limitations:

- No DatasetVersion model or lineage tracking
- Parquet versioning not strictly immutable
- Universe versioning not fully deterministic
- Repositories allow updates instead of append-only writes
- Audit log lacks full event envelope and tamper-evidence
- RunManifest immutability not enforced
- No enforcement of dataset coverage or integrity checks

As a result, full reproducibility and audit guarantees are not yet achieved.
