# v1 Boundaries (Locked)

## v1 Definition (Vertical Slice)
v1 is a single end-to-end vertical slice of a retail algorithmic trading platform for U.S. equities.

### Locked Assumptions (Non-Negotiable)
- Interval: **5-minute bars** (baseline)
- Broker: **Alpaca only**
- Capital: **single capital bucket**
- Strategy: **single strategy**
- Universe: **single defined universe** (free-data compatible)
- Evaluation model: **bar-based only** (no tick-level execution logic)
- Mode: **paper trading first** (live trading explicitly gated/disabled)

## Explicit Non-Goals (v1 Exclusions)
The following are explicitly out of scope for v1:
- Multiple brokers
- Multiple strategies / portfolio-of-strategies allocator
- Live trading enablement (beyond explicit architecture spec + gates)
- Tick-level strategies / market making / low-latency execution
- Advanced regime detection or adaptive intelligence controlling execution
- Cross-strategy diversification or capital allocation layers
- Complex margin/shorting support (only considered later with explicit scope)

## v1 “Done” Means
v1 is complete when:
- Paper trading vertical slice runs on a schedule (5-minute cadence)
- Orders are idempotent and restart-safe
- Safety doctrine is enforced by design (default NO_LIVE_TRADING, gates, allowlists)
- RunManifest enables reproducible runs
- Reconciliation detects mismatches and freezes trading safely