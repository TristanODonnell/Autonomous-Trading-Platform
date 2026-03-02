# Postgres System-of-Record (SoR) — Planning Spec (v1)

## Purpose
Postgres is the **system-of-record** for all transactional truth required to reproduce, audit, and reconcile runs:
- Run identity + reproducibility anchors (RunManifest)
- Order lifecycle + broker outcomes
- Fills + ledger events
- Periodic state snapshots (positions/cash/risk)
- Version pointers to immutable datasets stored in Parquet

Parquet stores historical datasets (bars, corporate actions, universe snapshots). Postgres stores **pointers + provenance + lifecycle events**. :contentReference[oaicite:1]{index=1}

## Non-Negotiables
- **Every run has exactly one RunManifest**.
- All operational entities link back to `run_id`.
- **Order lifecycle is event-driven**: transitions are recorded as immutable events.
- System must support: “Given a RunManifest, re-run the exact simulation later.” :contentReference[oaicite:2]{index=2}

## Table Inventory (Planning Level)

### 1) `runs`
**Role:** Top-level record for an execution (backtest, paper, or live-locked).

**Key fields (conceptual):**
- `run_id` (UUID, PK)
- `run_type` (enum: `backtest | paper | live` — v1 defaults paper/shadow)
- `status` (enum: `created | running | succeeded | failed | canceled`)
- `created_at`, `started_at`, `ended_at` (UTC)
- `environment` (paper/live identifiers, broker account allowlist pointer)
- `notes` (text)

**Invariants:**
- `run_id` is globally unique.
- Status transitions are append-only in audit log (see `audit_log` doc).

---

### 2) `run_manifest`
**Role:** Reproducibility anchor. Captures the exact versions of *data + config + code* for a run.

**Key fields (conceptual):**
- `run_id` (PK/FK to `runs`)
- `git_commit`
- `strategy_id` + `strategy_version`
- `strategy_config` (json)
- `dataset_version_id` (FK)
- `universe_version_id` (FK)
- `cost_model` (name + params json)
- `fill_model` (name + params json)
- `capital_bucket` (numeric)
- `random_seed`
- `start_time_utc`, `end_time_utc`
- `created_at`

**Invariants:**
- Exactly 1 manifest per run.
- Manifest references immutable dataset/universe versions (see below). :contentReference[oaicite:3]{index=3}

---

### 3) `orders`
**Role:** Canonical internal order record created from an OrderIntent.

**Key fields (conceptual):**
- `order_id` (UUID, PK)
- `run_id` (FK)
- `strategy_id`
- `symbol`
- `side` (`buy|sell`)
- `order_type` (`market|limit` for v1)
- `qty` and/or `notional`
- `limit_price` (nullable)
- `time_in_force` (v1: `day`)
- `extended_hours` (bool)
- `idempotency_key` (unique constraint per run/strategy scope)
- `created_at`

**Invariants:**
- Orders are **deduplicated** via `idempotency_key`. :contentReference[oaicite:4]{index=4}
- No “silent mutation” of core economic fields; changes must be new orders or event annotations.

---

### 4) `broker_order_events`
**Role:** Append-only event stream representing broker lifecycle updates (submitted, partial fill, cancel, reject, etc.).

**Key fields (conceptual):**
- `event_id` (UUID, PK)
- `run_id` (FK)
- `order_id` (FK to `orders`)
- `broker_order_id` (string)
- `event_type` (enum: `created|submitted|partially_filled|filled|canceled|rejected|error`)
- `event_time_utc`
- `payload` (json: broker response snapshot)
- `sequence_num` (monotonic per broker_order_id for ordering)

**Invariants:**
- Append-only (no updates, no deletes).
- Total order state can be derived by folding events in time/sequence order.
- This is the ground truth for “what the broker said happened.”

---

### 5) `fills`
**Role:** Canonical executions. In many designs, fills are derived from broker events; in v1 we persist normalized fill rows for reporting and reconciliation.

**Key fields (conceptual):**
- `fill_id` (UUID, PK)
- `run_id` (FK)
- `order_id` (FK)
- `broker_trade_id` (string, unique if provided)
- `fill_time_utc`
- `symbol`, `side`
- `quantity`, `price`
- `commission`, `fees`, `slippage` (optional fields; sim fills may include modeled values)
- `payload` (json)

**Invariants:**
- Fills are immutable records.
- Sum(fills.quantity) for an order must never exceed original requested quantity (unless explicitly modeled).

---

### 6) `position_snapshots`
**Role:** Periodic snapshot of holdings at evaluation boundaries (e.g., each 5-minute bar close).

**Key fields (conceptual):**
- `snapshot_id` (UUID, PK)
- `run_id` (FK)
- `as_of_time_utc` (bar-aligned)
- `positions` (json or normalized child table `position_snapshot_rows`)
- `source` (`internal|broker`)

**Invariants:**
- `as_of_time_utc` aligns to evaluation cadence.
- Snapshot captures the state used for decisioning & audit. :contentReference[oaicite:5]{index=5}

---

### 7) `cash_snapshots`
**Role:** Cash/buying power state at evaluation time.

**Key fields:**
- `snapshot_id`, `run_id`, `as_of_time_utc`
- `cash`, `buying_power`, `reserved`, `currency`
- `source` (`internal|broker`)

---

### 8) `risk_snapshots`
**Role:** Risk metrics computed at evaluation boundaries.

**Key fields:**
- `snapshot_id`, `run_id`, `as_of_time_utc`
- `gross_exposure`, `net_exposure`, `leverage`
- `drawdown`, `volatility_estimate`
- `risk_flags` (json array)

**Invariant:**
- Risk gating decisions must be explainable from these snapshots + manifest. :contentReference[oaicite:6]{index=6}

---

### 9) `universe_snapshots` (versioned)
**Role:** Time-aware universe membership to prevent survivorship/look-ahead bias.

**Key fields:**
- `universe_version_id` (PK)
- `snapshot_date` (date)
- `membership_hash` (hash of ordered symbol list)
- `criteria` (json)
- `source` (string metadata)
- `dataset_pointer` (e.g., parquet path for membership list)
- `created_at`

**Invariants:**
- Universe versions are immutable; membership changes create a new version. :contentReference[oaicite:7]{index=7}

---

### 10) `dataset_versions` (metadata + lineage)
**Role:** Registry for immutable Parquet datasets (bars, corporate actions, universe membership files).

**Key fields:**
- `dataset_version_id` (PK)
- `dataset_name` (e.g., `bars_raw_5m`, `bars_adj_5m`, `corporate_actions`)
- `source` (vendor/provider)
- `ingested_at_utc`
- `coverage_start_utc`, `coverage_end_utc`
- `schema_version`
- `content_checksum` (manifest hash of file checksums)
- `storage_uri` (root parquet folder)
- `lineage` (json: upstream dataset_version_ids + transform descriptors)

**Invariants:**
- DatasetVersion rows are immutable references to immutable storage.
- RunManifest must reference the exact dataset_version_id(s). :contentReference[oaicite:8]{index=8}

## Cross-Table Linkage Summary
- `runs.run_id` → parent for everything.
- `run_manifest.run_id` → anchors reproducibility.
- `orders.run_id` → generated decisions.
- `broker_order_events.order_id` → broker lifecycle.
- `fills.order_id` → executions.
- `*_snapshots.run_id` → state at decision boundaries.
- `run_manifest.dataset_version_id` + `run_manifest.universe_version_id` → locate Parquet artifacts.

## Planning Notes / v1 Simplifications
- Keep schemas minimal; normalize later if needed.
- Prefer append-only + event sourcing for lifecycle where possible.
- Don’t over-index early; only add indexes required by access patterns once defined.
