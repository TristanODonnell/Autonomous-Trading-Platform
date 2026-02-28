# Compile-Time vs Runtime Semantics

## Purpose
This document defines what is validated/frozen before a run ("compile-time") versus what occurs during an active run ("runtime").
This boundary exists to support reproducibility, auditability, and safe execution.

---

## Compile-Time (Pre-Run / Frozen Inputs)
Compile-time is everything that can be validated and fixed before execution begins.

### Responsibilities
- Validate configuration schema and required fields
- Select and lock:
  - UniverseSnapshot version
  - Dataset version(s) (bars + corporate actions)
  - Strategy configuration parameters
- Build schedule plan for evaluation cadence (5-minute boundaries)
- Resolve environment:
  - paper vs live (must default to NO_LIVE_TRADING)
  - broker account allowlist selection
- Produce a frozen RunManifest draft (all immutable inputs)

### Output Artifacts
- Config snapshot (exact file contents)
- Dataset version identifiers + checksums
- Universe snapshot identifier + membership hash
- RunManifest (immutable inputs)

---

## Runtime (During Run / Stateful Processes)
Runtime is everything that happens on each evaluation cycle.

### Responsibilities
- Ingest the next bar window and validate SLAs
- Compute signals using bar data
- Apply risk gates and generate OrderIntents
- Execute through broker adapter (paper by default; live requires gates)
- Track order state machine transitions
- Update ledger and snapshots (positions/cash/risk)
- Reconcile against broker positions/fills
- Emit audit logs and reporting outputs

### Output Artifacts
- OrderIntent records
- BrokerOrder and Fill records
- Position/Cash/Risk snapshots
- Reconciliation events
- Run summary reports