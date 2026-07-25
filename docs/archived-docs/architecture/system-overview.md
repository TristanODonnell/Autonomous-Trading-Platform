# System Overview

## Summary

The Autonomous Trading Platform is a layered system for deterministic, auditable trading workflows operating on a 5-minute bar cadence.

The current implementation supports a paper-trading runtime slice including:

- market data ingestion
- strategy evaluation
- order intent generation
- order submission
- order reconciliation
- portfolio and risk state tracking
- audit and run metadata persistence

The system is structured for reproducibility, separation of concerns, and future expansion into observability, research, and execution intelligence.

---

## Current Runtime Shape

The active trading cycle executes the following sequence:

1. ingestion readiness check
2. strategy evaluation
3. order submission
4. order reconciliation
5. risk snapshot generation

This represents a minimal but functional orchestration loop.

The full runtime model described in earlier specifications (pre-evaluation reconciliation, strict SLA enforcement, full freeze semantics) is not yet implemented.

---

## Architecture

The system is organized into the following domains:

- contracts
- ingestion
- universe
- strategy
- execution
- safety
- storage
- scheduler / orchestration

Each domain encapsulates a specific responsibility and exposes services consumed by orchestration layers.

Jobs compose domain services into executable units.
Cycles coordinate jobs into end-to-end workflows.

---

## Runtime Model

The platform operates on discrete 5-minute evaluation windows.

At each cycle:

- market data is ingested and normalized
- strategy logic evaluates the current context
- signals are converted into order intents
- intents pass through safety checks
- eligible orders are submitted to the broker
- broker state is reconciled
- fills update portfolio state
- a risk snapshot is recorded
- run state is updated via the run manifest

Airflow DAGs schedule the primary cycles:

- trading cycle (5-minute cadence)
- ingestion cycle (5-minute cadence)
- corporate action ingestion (daily)
- backfill (daily)

---

## Persistence Model

The platform uses a hybrid storage model:

- **Postgres (system of record)** for operational state
- **Parquet datasets** for historical data

Persisted entities include:

- run manifests
- market data
- corporate actions
- universe snapshots
- broker orders
- fills
- position, cash, and risk snapshots
- audit log events

Versioning and immutability are partially implemented. Full dataset lineage and append-only guarantees are not yet enforced.

---

## Safety Model

Safety controls exist to prevent unintended execution and duplicate activity.

Implemented components include:

- idempotency key generation
- order throttling
- exposure checks
- environment gating
- runtime gate
- kill switch
- shadow mode

Enforcement is incomplete:

- some checks rely on stubbed data sources
- gates are not consistently applied at execution boundaries
- kill switch and runtime gate are not externally persisted

---

## Reproducibility and Audit

The system records:

- run manifests
- audit log events
- broker order and fill records
- portfolio snapshots

These provide traceability for execution flows.

However, the following are not yet fully implemented:

- append-only guarantees
- deterministic dataset lineage
- full audit event schema
- tamper-evident event chaining

---

## Known Gaps

The current system does not yet implement:

- pre-evaluation reconciliation
- data-aware ingestion readiness
- full freeze and recovery workflow
- scheduler event sequencing
- strict append-only storage
- corporate action continuity enforcement
- deterministic dataset versioning
- full universe enforcement during evaluation

---

## Scope

This document reflects the current implementation.

Deferred behavior is intentionally excluded from the core runtime description unless explicitly noted.
