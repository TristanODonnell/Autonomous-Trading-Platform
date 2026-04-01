# UniverseVersion (Contract)

## Purpose

`UniverseVersion` is an immutable identity record representing a time-aware universe membership snapshot.

It exists to:

- Eliminate survivorship bias
- Eliminate look-ahead bias
- Allow exact reconstruction of strategy universe
- Provide audit-grade traceability

A `RunManifest` must reference a specific universe_version_id. :contentReference[oaicite:2]{index=2}

---

## Canonical Fields

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| universe_version_id | string | Yes | Globally unique identifier for this universe snapshot |
| snapshot_date | date | Yes | Effective date of membership |
| membership_list_hash | string | Yes | Deterministic hash of membership list |
| membership_count | integer | Yes | Number of securities in the universe |
| selection_criteria | json | Yes | Rules used to construct the universe |
| source | string | Yes | Source of candidate universe (e.g., alpaca_iex) |
| source_metadata | json | Yes | Feed metadata, filters, parameters |
| storage_uri | string | Yes | Location of stored membership artifact |
| created_at_utc | datetime | Yes | Creation timestamp |
| notes | string | No | Optional metadata |

---

## Membership Hash Specification

`membership_list_hash` must be computed deterministically.

Required normalization steps:

1. Normalize symbols (uppercase, trimmed)
2. Sort lexicographically
3. Join with newline separator
4. Hash resulting string (e.g., SHA256)

Rules:

- Identical membership → identical hash
- Any symbol addition/removal → new hash
- universe_version_id must change if hash changes

---

## Selection Criteria (v1 Required Fields)

Recommended minimum keys:

```json
{
  "universe_base": "alpaca_iex",
  "target_size": 500,
  "price_floor": 1.00,
  "min_avg_dollar_volume": 5000000,
  "lookback_window_days": 20,
  "rebalance_frequency": "static"
}
