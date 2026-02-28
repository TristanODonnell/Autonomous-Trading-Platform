# Autonomous-Trading-Platform

## Canonical Docs
- docs/v1-boundaries.md
- docs/safety-doctrine.md
- docs/invariants.md
- docs/compile-vs-runtime.md
- docs/contracts/index.md
- docs/decisions.md

## Status
- Current Phase: Phase 1 — Canonical Contracts & Invariants (Spec Freeze)
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

Next: Phase 2 — Vertical Slice Implementation (Data → Signal → OrderIntent → BrokerOrder → Fill → Ledger)