# Parquet Dataset Storage Conventions — Planning Spec (v1)

## Purpose
Parquet is used for **immutable historical datasets** required for:
- backtests / scenario analysis
- reproducible replays
- audit of “what data the strategy saw”

Postgres stores dataset metadata + lineage (`dataset_versions`) and the run references them via `run_manifest`. :contentReference[oaicite:9]{index=9}

## Canonical Datasets (v1)
### 1) Market Bars — Raw
Dataset name: `bars_raw_5m`

- Represents broker-provided (or vendor-provided) bars without corporate action adjustments.
- Must preserve original timestamps and vendor fields.

### 2) Market Bars — Adjusted
Dataset name: `bars_adj_5m`

- Derived from `bars_raw_5m` + corporate actions.
- Must preserve both raw and adjusted availability (no overwriting raw). :contentReference[oaicite:10]{index=10}

### 3) Corporate Actions
Dataset name: `corporate_actions`

- Dividends, splits, mergers/spinoffs, name changes.
- Used to adjust bar prices and position quantities.
- Stored as its own immutable dataset version. :contentReference[oaicite:11]{index=11}

### 4) Universe Membership Snapshots (optional as parquet)
Dataset name: `universe_membership`

- Membership lists by snapshot date.
- Can be stored as parquet (and referenced by `universe_snapshots.dataset_pointer`).

## Folder & Versioning Scheme

### Storage Root
Assume a root like:

`data/`

### Dataset Root Convention
Each dataset has:

`data/<dataset_name>/`

Each immutable ingestion/production creates a new version folder:

`data/<dataset_name>/version=<dataset_version_id>/`

Examples:
- `data/bars_raw_5m/version=2026-02-27T000000Z_abc123/`
- `data/bars_adj_5m/version=2026-02-27T001500Z_def456/`
- `data/corporate_actions/version=2026-02-27T000500Z_ca7890/`

**Rules:**
- Version folder contents are immutable.
- A DatasetVersion row in Postgres points to the root `storage_uri` for that version.

## Partitioning Scheme (v1)
Partition by **symbol** and **date** for efficient reads and bounded scans.

Canonical partition columns:
- `symbol`
- `date` (UTC date derived from bar timestamp)

Path pattern:
`.../symbol=<SYMBOL>/date=<YYYY-MM-DD>/part-....parquet`

Example:
`data/bars_raw_5m/version=.../symbol=AAPL/date=2026-02-26/part-0000.parquet`

## Schema Notes (Planning-Level)
### Bars (Raw/Adjusted)
- `timestamp_utc` (bar start, aligned to 5m boundary)
- `symbol`
- `open`, `high`, `low`, `close`
- `volume`, `trade_count`, `vwap`
- For adjusted bars: `adjustment_factor` and/or adjusted OHLC fields

Alignment rule:
- Bars must be aligned to 5-minute boundaries and stored in UTC. :contentReference[oaicite:12]{index=12}

### Corporate Actions
- `effective_date`
- `symbol`
- `type`
- `ratio_or_amount`
- `new_symbol` (nullable)
- `metadata` (json-ish columns if needed)

## Raw vs Adjusted — Required Invariants
- Raw bars are never overwritten.
- Adjusted bars are reproducible from:
  - raw bars dataset version
  - corporate actions dataset version
  - transformation spec version (recorded in `dataset_versions.lineage`)
- RunManifest must record exactly which dataset versions were used. :contentReference[oaicite:13]{index=13}

## “Locate Data From RunManifest” Procedure
Given:
- `run_manifest.dataset_version_id`
- `run_manifest.universe_version_id`

You can:
1. Look up `dataset_versions.storage_uri` for bars + corporate actions.
2. Read parquet partitions for requested symbols and date window.
3. Load universe membership for the run’s snapshot date/version.
4. Replay evaluation exactly.

This is the acceptance requirement for reproducibility. :contentReference[oaicite:14]{index=14}
