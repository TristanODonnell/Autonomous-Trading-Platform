# Phase 9 — Paper Trading Acceptance Criteria

## Objective

Define the minimum proof required before v1 paper trading is considered operationally valid.

Paper trading is not considered “working” until it has demonstrated sustained correctness under live broker connectivity and restart conditions.

---

## Minimum Run Window

The strategy must run continuously for:

- 10 consecutive market days (minimum)
- During regular trading hours (09:30–16:00 ET)
- Using the Alpaca paper trading account
- With real broker API connectivity

Restarts are required and must be part of validation.

If a critical safety failure occurs, the validation window resets.

---

## Required Outcomes

The following must hold true for the entire run window:

### 1. Zero Safety Gate Violations

No violations of:

- Per-symbol exposure caps
- Total capital bucket cap
- Max concurrent positions
- Unsupported order types
- Extended-hours constraints (if disabled)

Any violation invalidates the window.

---

### 2. Zero Reconciliation Mismatches

At every evaluation cycle:

Internal PositionSnapshot must match broker-reported positions.

If a mismatch occurs:

- Trading must halt immediately
- An incident must be recorded
- Manual investigation required
- Validation window resets

---

### 3. Idempotency Proven Under Restarts

The system must be restarted at least three times during the validation window.

After restart:

- No duplicate OrderIntent is generated
- No duplicate broker submission occurs
- Previously filled orders are not resent
- Exposure constraints remain respected

OrderIntent identity must be stable across restart boundaries.

---

### 4. Complete Logging and Audit Trail

For every order lifecycle:

- Signal recorded
- OrderIntent recorded
- BrokerOrder recorded
- Fill events recorded
- State transitions recorded

Each run must produce a RunManifest containing:

- git_commit
- dataset_version
- universe_version
- strategy_version
- environment=paper
- configuration snapshot

Missing audit artifacts invalidate the window.

---

## Acceptance Definition

Paper trading is considered valid when:

- All required outcomes hold for the full run window
- No ambiguous order states are observed
- No silent failures occur
- Restart behavior is stable and idempotent