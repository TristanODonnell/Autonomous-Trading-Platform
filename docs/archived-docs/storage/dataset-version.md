# DatasetVersion (Contract)

## Purpose

`DatasetVersion` is an immutable metadata record that uniquely identifies a frozen dataset instance stored in immutable storage (e.g., Parquet).

It exists to guarantee:

- Reproducibility of any run
- Auditability of historical simulations
- Deterministic data lineage
- Integrity validation via checksums

A `RunManifest` must reference specific `DatasetVersion` identifiers. :contentReference[oaicite:1]{index=1}

---

## Scope (v1)

Applies to dataset families such as:

- bars_raw_5m
- bars_adj_5m
- corporate_actions
- universe_membership (if stored in Parquet)

Each ingestion or transformation produces a new immutable DatasetVersion.

---

## Canonical Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| dataset_version_id | string | Yes | Globally unique identifier for this dataset instance |
| dataset_name | string | Yes | Logical dataset family name (e.g., bars_raw_5m) |
| source | string | Yes | Data provider + feed identifier |
| ingested_at_utc | datetime | Yes | Timestamp when dataset version was produced |
| coverage_start_utc | datetime | Yes | Inclusive lower bound of dataset coverage |
| coverage_end_utc | datetime | Yes | Exclusive upper bound preferred (or explicitly documented) |
| schema_version | string | Yes | Dataset schema version identifier |
| storage_uri | string | Yes | Root path to immutable storage for this dataset version |
| content_checksum | string | Yes | Deterministic hash of dataset contents |
| partition_scheme | string | Yes | Logical partitioning strategy (e.g., symbol/date) |
| lineage | json | Yes | Provenance metadata describing upstream dependencies |
| notes | string | No | Optional human-readable metadata |

---

## Checksum Specification

`content_checksum` must be reproducible and integrity-verifiable.

Recommended implementation:

1. Enumerate all files under `storage_uri`
2. Compute file-level checksums
3. Build a sorted manifest of:
   - relative_path
   - file_size
   - file_hash
4. Hash the manifest deterministically (e.g., SHA256)

Any change to underlying files MUST produce a new dataset_version_id.

---

## Lineage Specification

`lineage` must describe how the dataset was derived.

Minimum structure:

```json
{
  "upstream_dataset_versions": ["..."],
  "transform_name": "string",
  "transform_version": "string",
  "parameters": { }
}
