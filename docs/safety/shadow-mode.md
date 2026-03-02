# Shadow Mode Specification

## Objective
Define a non-executing operating mode used to validate strategy logic end-to-end
without any broker interaction.

Shadow mode must guarantee:
- Signals and OrderIntents are produced deterministically
- No broker calls occur (no orders, no cancels, no account queries)
- All risk gates and caps still evaluate (to surface violations early)

This is a safety and validation mode, not a performance mode.

---

## Definition

**Shadow mode** is an execution mode where:

- Strategy evaluation runs normally
- Signals are generated normally
- OrderIntents are generated normally
- Risk/cap validation runs normally
- OrderIntents are persisted and audited
- **BrokerAdapter is disabled and must never initialize**
- **No network calls to broker endpoints are permitted**

Shadow mode is required before enabling paper trading for a strategy.

---

## Scope (What Runs)

Shadow mode MUST run:

1. Market data ingestion (read-only)
2. UniverseSnapshot resolution (time-aware)
3. Strategy evaluation at bar cadence
4. Signal generation + persistence
5. OrderIntent construction + persistence
6. Idempotency key generation + dedupe checks
7. Risk checks + caps (gross, per-symbol, daily notional, order rate)
8. Audit logging for each step

Shadow mode MUST NOT run:

- Broker order submissions
- Broker cancel requests
- Broker position reconciliation calls
- Broker account or buying-power queries
- Any broker WebSocket connections

---

## Output Artifacts (Required)

Shadow mode produces the same artifacts as paper/live except broker artifacts:

### Required
- Signal stream (Signal contract objects)
- OrderIntent stream (OrderIntent contract objects)
- RiskSnapshot per evaluation step
- RunManifest with environment=shadow
- Audit log entries:
  - evaluation start/end
  - gating decisions
  - cap validation results
  - OrderIntent acceptance/rejection reason

### Prohibited
- BrokerOrder objects
- Fill objects

---

## State Machine / Execution Semantics

Shadow mode is a "dry run" of decision logic, not a simulation of fills.

- No position changes are applied from fills (because no fills exist)
- PositionSnapshot may remain "unchanged" or be computed hypothetically
  but MUST be clearly labeled as hypothetical in audit logs

Recommendation:
- Keep PositionSnapshot as the last known broker state ONLY for paper/live.
- In shadow, PositionSnapshot = "shadow_state" computed from internal ledger,
  but must not be treated as real.

---

## Safety Invariants (Non-Negotiable)

1. **BrokerAdapter must not initialize**
2. **Any attempt to call broker code path = hard fail**
3. Shadow mode must run ALL risk gates except broker-specific ones
4. All OrderIntents must be persisted (even rejected ones, with reason)
5. Shadow mode must set:
   - RunManifest.environment = "shadow"
   - NO_LIVE_TRADING = true (implicitly)

---

## Failure Modes

### If broker credentials are present in environment
Still must not matter. Shadow mode must ignore them.

### If config mistakenly sets environment=paper
Shadow mode wins if explicitly enabled. Precedence rules must be explicit:
- If SHADOW_MODE=true → environment forced to shadow.

---

## Acceptance Tests (Planning-Level)
- Strategy produces OrderIntents for N bars
- Risk validation logs show cap checks executed
- No BrokerOrder or Fill artifacts exist
- Any attempt to enable broker in shadow causes a hard stop
