# Layering

## Overview

The platform follows a layered architecture separating data definitions, domain logic, persistence, and orchestration.

This structure defines ownership boundaries and dependency direction across the system.

---

## Layers

The system is organized into:

1. Contracts
2. Domain Services
3. Storage
4. Orchestration / Scheduler
5. Interfaces
6. External Systems

Cross-cutting concerns include safety, audit logging, and configuration.

---

## Contracts

Defines canonical data structures shared across all domains.

Examples:

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
- AuditLogEvent

Responsibilities:

- schema definition
- validation rules
- shared invariants

Constraints:

- no orchestration logic
- no persistence logic
- no external integration logic

Dependencies:

- no dependency on higher layers

---

## Domain Services

Implements business logic within each domain.

Includes:

- ingestion services
- universe services
- strategy services
- execution services
- safety services
- reconciliation services
- ledger services
- snapshot services

Responsibilities:

- data transformation
- rule enforcement
- state transitions
- signal generation
- order construction
- reconciliation logic
- portfolio updates

Constraints:

- no CLI or Airflow wiring
- no top-level orchestration

Dependencies:

- contracts
- repositories / storage interfaces
- adapters for external systems
- configuration

---

## Storage

Provides persistence for system state and datasets.

Includes:

- ORM models
- repositories
- unit-of-work patterns
- parquet dataset handling

Responsibilities:

- data storage and retrieval
- transaction handling
- mapping between contracts and storage models

Constraints:

- no domain decision logic
- no orchestration logic

Dependencies:

- contracts
- storage infrastructure libraries

---

## Orchestration / Scheduler

Coordinates multi-step workflows across domains.

Includes:

- trading cycle
- ingestion cycle
- corporate action cycle
- backfill cycle
- scheduler jobs

Responsibilities:

- step sequencing
- dependency wiring
- run lifecycle progression
- degraded-path handling

Constraints:

- no low-level domain logic
- no storage implementation details

Dependencies:

- domain services
- storage
- contracts
- configuration

---

## Interfaces

Defines system entry points.

Includes:

- CLI commands
- Airflow DAGs

Responsibilities:

- triggering workflows
- parameter handling

Constraints:

- no business logic
- no domain rules

Dependencies:

- orchestration layer

---

## External Systems

Represents integrations outside the platform.

Examples:

- market data providers
- broker APIs
- Postgres
- parquet storage
- Airflow runtime

External interactions are isolated behind adapters or service boundaries.

---

## Dependency Direction

The intended dependency flow is:

`contracts -> domain services -> orchestration -> interfaces`

Storage supports domain services and orchestration but remains persistence-focused.

Lower layers must not depend on higher layers.

---

## Domain Ownership

- **Ingestion**: market data and corporate action processing
- **Universe**: symbol selection and snapshot membership
- **Strategy**: signal generation and lifecycle state
- **Execution**: order lifecycle and fill handling
- **Safety**: gating, idempotency, throttles, caps
- **Storage**: persistence only
- **Scheduler**: workflow coordination

---

## Current Boundary Notes

The layering model is in place but not strictly enforced in all areas.

Current deviations include:

- repositories supporting upsert instead of append-only behavior
- orchestration relying on stubbed readers in safety and risk paths
- partial enforcement of safety and freeze semantics
- simplified versioning and audit guarantees

These reflect implementation maturity rather than architectural changes.
