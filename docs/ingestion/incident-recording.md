# Ingestion Incident Recording (v1)

## Purpose

All ingestion failures, SLA misses, missing data, and outliers must be recorded as immutable events.

## Event Types

- INGESTION_SLA_PASSED
- INGESTION_SLA_MISSED
- MARKETBAR_MISSING
- MARKETBAR_OUTLIER_DETECTED
- MARKETBAR_INVALID_REJECTED
- CORPORATE_ACTION_CONTINUITY_BREACH
- PROVIDER_RATE_LIMITED
- PROVIDER_TIMEOUT
- PROVIDER_BAD_RESPONSE

## Required Event Fields

- event_id (uuid)
- event_time_utc
- run_id
- dataset_version (nullable if not yet finalized)
- universe_version
- bar_timestamp (nullable for non-bar events)
- symbol (nullable)
- event_type
- severity (INFO/WARN/CRITICAL)
- action_taken (SKIP_SYMBOL / DEGRADE_SAFE_MODE / HALT_CYCLE)
- details (json)

## Acceptance Mapping

For any bar timestamp T, the system must be able to answer:

1) When it must be available:
   - bar_close_time + 30s (target)
   - bar_close_time + 90s (hard deadline)

2) What happens if it isn’t:
   - SKIP / DEGRADE / HALT decision tree applied deterministically

3) How the incident is recorded:
   - one of the event types above with required fields