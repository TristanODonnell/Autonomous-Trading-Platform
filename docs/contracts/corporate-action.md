# CorporateAction

## Purpose
- Canonical representation of corporate actions (splits/dividends/mergers/name changes) used to:
  - adjust historical market data for continuity (raw vs adjusted series)
  - keep position/ledger quantities and cost basis consistent across actions
  - prevent strategy decisions from being distorted by structural price jumps

## Producer / Consumer
- Produced by: Corporate Action Ingestion (Data Provider Adapter) + Normalization Layer
- Consumed by:
  - Market Data Adjustment Pipeline (builds adjusted bars / factors)
  - Ledger / Position Adjuster (applies split ratios to holdings)
  - Backtester (ensures historical continuity)
  - Reconciliation / Audit (verifies broker vs internal adjustments)

## Schema (Canonical Fields)
| Field | Type | Required | Description |
|---|---|---:|---|
| `action_id` | string | yes | Deterministic ID (provider id if available; else hash of key fields). |
| `symbol` | string | yes | Symbol before action. |
| `type` | enum | yes | `cash_dividend`, `stock_dividend`, `split_forward`, `split_reverse`, `spinoff`, `merger_cash`, `merger_stock`, `name_change`. |
| `effective_date` | date | yes | Date action becomes effective in market data / positions. |
| `announced_date` | date | no | When action announced (if available). |
| `record_date` | date | no | Record date (dividends). |
| `payable_date` | date | no | Pay date (dividends). |
| `ratio_or_amount` | float | yes | Split ratio (e.g., 10.0 for 10:1) or dividend $ amount. |
| `new_symbol` | string | no | For name change/merger. |
| `currency` | string | no | For cash dividend. |
| `source` | string | yes | Provider identifier. |
| `ingested_at` | datetime (UTC) | yes | Lineage. |
| `metadata` | json | no | CUSIP, notes, etc. |

## Invariants (Must Always Be True)
- `effective_date` is present and valid date.
- `ratio_or_amount > 0`.
- If `type` is a split: `ratio_or_amount != 1.0`.
- If `type="name_change"` then `new_symbol` must be present.
- `action_id` is unique (idempotent upsert).
- `action_id` is treated as the stable identity key; updates are allowed only as new revisions in audit logs (append-only history), never silent overwrite without trace.
- For split actions, cumulative adjustment factors derived from CorporateAction events must produce a continuous adjusted price series (no artificial price discontinuity at the effective boundary).

## Validation Rules (Planning-Level)
- Check: unknown action `type` => quarantine event + alert (do not apply).
- Check: if provider sends duplicates with changed metadata, keep latest but preserve prior versions in audit log.
- On failure: **halt adjustment pipeline** (corporate actions are “high-impact correctness”).

## Versioning
- `schema_version`: integer (start at `1`). Increment only when fields, types, or invariants change.
- Backward compatibility rules:
  - Additive fields are allowed (consumers must ignore unknown fields).
  - Any rename/removal/type change/semantic change requires `schema_version += 1`.
- Dataset lineage:
  - Runs reference the corporate-action dataset build via `RunManifest.dataset_version` (or a dedicated `corporate_action_dataset_version` if you split datasets later).
