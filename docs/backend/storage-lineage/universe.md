# Domain: Universe

## Overview

The universe system defines the set of tradable symbols used by the platform at any given time.

It is responsible for:

- selecting eligible symbols
- generating time-aware snapshots of membership
- ensuring reproducibility for historical runs
- preventing survivorship bias

Universe membership is determined on a scheduled cadence and stored as versioned snapshots.

---

## Universe Definition (Selection Rules)

The current universe is derived from Alpaca-supported U.S. equities.

Selection behavior:

- Symbols are filtered based on:
  - minimum price threshold
  - minimum liquidity (volume / dollar volume)
- Only actively tradable symbols are included
- Universe selection runs on a periodic cycle (e.g., monthly or manual trigger)

The selection process produces a list of eligible symbols and associated selection criteria.

---

## Snapshot Model

Universe membership is stored as discrete snapshots.

Each snapshot contains:

- snapshot_date
- symbols[]
- version
- metadata (criteria, source, timestamps)

At runtime:

- The system retrieves the active snapshot for a given timestamp
- Universe membership is resolved as-of the evaluation time

This ensures:

- time-aware membership
- consistent symbol sets during execution

---

## Lifecycle Handling

The system supports basic handling of symbol lifecycle events:

- ticker changes (symbol remapping)
- delistings (symbol remains in historical snapshots)
- mergers and corporate events (partially supported)

Current behavior:

- historical data remains stored under original symbol
- mapping may be applied for forward continuity
- lifecycle handling is limited and not fully enforced

---

## Guarantees & Constraints

The intended design enforces:

- time-aware universe membership
- no use of future information (no lookahead bias)
- reproducible membership for historical runs
- deterministic selection based on data + filters

However, not all guarantees are fully enforced in the current implementation.

---

## Current Behavior

The current implementation provides:

- universe selection based on basic filters
- snapshot creation and storage
- symbol normalization (uppercase, deduplication)
- retrieval of active symbols for a given timestamp

Snapshot behavior:

- snapshots are stored in the database
- previous snapshots may be closed when new ones are created
- version is derived from symbol list only

---

## Limitations

The current universe system is a simplified implementation of the intended design.

Key limitations:

- universe_id is randomly generated (not deterministic)
- version does not include:
  - selection criteria
  - snapshot date
- snapshots may be updated or overwritten (not strictly immutable)
- no enforcement of non-overlapping snapshot windows
- lifecycle handling is incomplete (mergers, delistings, mappings)
- no strict enforcement of survivorship bias guarantees
- universe size validation is minimal
