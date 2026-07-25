# Safety Doctrine (Capital Protection Non-Negotiables)

## Purpose
The system must be designed so it can never accidentally deploy real capital due to a software bug.
This requirement overrides convenience and feature velocity.

---

## Safety Invariants (Must Always Hold)

### 1) Environment Isolation (Paper vs Live)
- Paper and Live must be isolated by:
  - Separate credentials
  - Separate configuration namespaces
  - Explicit account allowlists
- Paper orders must never be routable to live accounts.

### 2) Default NO_LIVE_TRADING
- Default mode is **NO_LIVE_TRADING**.
- If the system cannot confirm safe enablement, it must behave as:
  - Shadow mode (signal-only), or
  - Halt (no broker calls)

### 3) Multi-Layer Live Enablement Gates
Live trading requires multiple independent gates (minimum 4 layers):
- Build/feature gate
- Config gate
- Runtime approval gate (human-controlled / short-lived token)
- External kill switch gate (out-of-band override)

### 4) Broker Account Allowlist
- Orders may only be sent to allowlisted broker account IDs.
- Unknown account ID => hard failure, no execution.

### 5) Hard Exposure Caps (Pre-Trade)
- Enforce hard caps before any broker call:
  - Max gross exposure
  - Max per-symbol exposure
  - Max daily notional
  - Max order frequency (per bar / per hour)
- If caps cannot be computed reliably -> halt.

### 6) Idempotent Execution + Deduplication
- Every OrderIntent must include a deterministic idempotency key.
- Duplicate submissions for the same key must be prevented.

### 7) Kill Switch Outside the Main DB
- A kill switch must exist outside the main DB.
- When activated, it must:
  - Stop new order submissions immediately
  - Attempt cancel of open orders (best effort)
  - Freeze trading until human reset

### 8) Shadow Mode
- Shadow mode must exist:
  - Signal generation + OrderIntent generation
  - No broker calls
  - Full logging / reporting
- Shadow mode is the default safe fallback.

### 9) Broker Reconciliation is Mandatory
- Reconciliation runs before/after execution cycles (defined schedule).
- Any mismatch (positions, fills, cash) triggers:
  - Freeze trading
  - Alert escalation
  - Human intervention required to resume
