# Data Flow

## Overview

This document defines the current end-to-end data flow through the platform.

It reflects the implemented runtime path.

---

## High-Level Flow

`Provider -> Ingestion -> Storage -> Strategy -> OrderIntent -> Safety -> Execution -> Broker -> Reconciliation -> Portfolio State -> Risk Snapshot -> Audit / Manifest`

---

## Ingestion

External data is retrieved from providers:

- market data (minute bars)
- corporate actions

Ingestion produces canonical data:

- aggregates minute bars into 5-minute MarketBar records
- validates alignment and basic integrity
- flags anomalies (missing, late, outlier)
- persists bars and audit events

Corporate actions are processed separately:

- normalized
- partially supported types applied (e.g. splits)
- adjustments written to historical data

---

## Storage

Ingestion outputs are persisted as:

- market bars
- corporate actions
- adjusted datasets
- audit events

Operational state is stored in the system of record.

Historical datasets may be written to parquet storage.

Versioning exists as identifiers but is not fully enforced.

---

## Universe

Universe selection produces snapshots of eligible symbols.

- snapshots are stored and retrievable by timestamp
- versioning is partially deterministic
- runtime enforcement within the trading cycle is incomplete

---

## Trading Cycle

The scheduler initiates a trading cycle on a 5-minute cadence.

Current step sequence:

1. ingestion readiness check
2. strategy evaluation
3. order submission
4. order reconciliation
5. risk snapshot

A run manifest tracks cycle state and progression.

---

## Readiness

Readiness is evaluated before strategy execution.

Current behavior:

- checks current time against ingestion deadline
- does not verify data completeness or dependencies

Failure handling:

- may skip evaluation (degraded mode)
- or fail the cycle

---

## Strategy

Strategy evaluation consumes current market context.

Flow:

`Market Data -> Signal -> OrderIntent`

- signals represent directional decisions
- order intents represent executable instructions
- strategy state is updated based on signal generation and intent creation

---

## Safety

Order intents pass through safety controls prior to execution.

Includes:

- idempotency checks
- throttling
- exposure checks
- shadow mode suppression
- partial live gating

Limitations:

- relies on stubbed readers for state
- gating not consistently enforced at submission boundary
- kill switch and runtime gate not externally persisted

---

## Execution

Order intents are submitted through the broker adapter.

Flow:

`OrderIntent -> BrokerOrder`

Order states include:

- NEW
- SUBMITTED
- PARTIALLY_FILLED
- FILLED
- CANCELED
- REJECTED

---

## Reconciliation

Broker state is reconciled after submission.

Flow:

`Broker -> Reconciliation -> BrokerOrder / Fill`

Current scope:

- reconciles tracked orders only
- extracts fills
- updates order status
- persists updates

No full portfolio or cash reconciliation is performed.

---

## Portfolio Updates

Fills trigger portfolio updates:

- position updates
- cash updates
- snapshot persistence

Outputs:

- PositionSnapshot
- CashSnapshot

---

## Risk Snapshot

At cycle end:

- risk metrics are computed
- snapshot is persisted

Includes:

- gross exposure
- net exposure
- leverage
- block status

Currently informational; not enforced as a blocking gate.

---

## Audit and Manifest

System activity is recorded through:

- run manifest updates
- audit log events
- order and fill records
- portfolio snapshots

Audit structure is simplified and not fully append-only.

---

## Current Flow

`Provider APIs`
-> `Ingestion`
-> `Market Data / Corporate Actions`
-> `Storage`
-> `Trading Cycle`
-> `Readiness`
-> `Strategy`
-> `OrderIntent`
-> `Safety`
-> `Execution`
-> `Broker`
-> `Reconciliation`
-> `Fill`
-> `Portfolio Updates`
-> `Risk Snapshot`
-> `Audit / Manifest`

---

## Deferred Flow Components

Not currently implemented:

- data-validated readiness
- pre-evaluation reconciliation
- universe enforcement during evaluation
- corporate action readiness gating
- symbol-level SLA handling
- freeze and recovery workflow
- scheduler event sequencing
- append-only event model
