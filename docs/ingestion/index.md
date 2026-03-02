# Ingestion Pipeline (v1) — Index

## Purpose

This folder locks the v1 ingestion pipeline behavior for:

- data sources and rate limits
- raw vs adjusted storage policy
- per-cycle SLAs and fallback behavior
- outlier + missing data policy
- corporate action continuity checks
- incident recording requirements

These rules are binding for v1 implementation.

## Canonical References

- `sources.md` — exact feeds and rate limits
- `slas-and-fallbacks.md` — freshness windows + breach actions
- `outliers-and-missing-data.md` — thresholds + missing bar semantics
- `corporate-actions-continuity.md` — adjustment rules + continuity checks
- `incident-recording.md` — event schema for ingestion incidents

## Non-Goals (v1)

- no paid feeds
- no multi-provider cross-validation
- no tick-level ingestion
- no L2/orderbook data