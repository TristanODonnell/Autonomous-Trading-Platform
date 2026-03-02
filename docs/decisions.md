# Decision Log (ADR-lite)

## Purpose
Record chosen defaults and rationale so the system remains stable and explainable.

Format:
- **ID**
- **Decision**
- **Status**: Proposed | Accepted | Deprecated
- **Rationale**
- **Consequences**
- **Date**

---

## D-001 — v1 Bar Interval = 5 Minutes
- Status: Accepted
- Rationale: Balances noise vs responsiveness; manageable ingestion and slippage modeling.
- Consequences: Not suitable for tick/microstructure strategies.

## D-002 — Broker = Alpaca Only (v1)
- Status: Accepted
- Rationale: Simplifies execution adapter and reconciliation.
- Consequences: Broker abstraction must exist but only one implementation in v1.

## D-003 — Default = NO_LIVE_TRADING
- Status: Accepted
- Rationale: Prevent accidental live capital deployment.
- Consequences: Live requires explicit gated enablement.

## D-004 — Postgres as System-of-Record
- Status: Accepted
- Rationale: Strong transactional guarantees; auditable state.
- Consequences: Data models must remain migration-friendly.

## D-005 — Parquet for Historical Datasets
- Status: Accepted
- Rationale: Efficient columnar storage and reproducibility.
- Consequences: Dataset versioning + checksums required.
