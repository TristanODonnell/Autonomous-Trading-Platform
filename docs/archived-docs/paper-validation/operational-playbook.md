# Phase 9 — Operational Playbook (Paper Mode)

## Objective

Define the required operational procedures for running v1 in paper trading mode.

This playbook governs incident response, kill switch testing, and manual intervention policy.

---

## Incident Response Procedure

If any of the following occur:

- Reconciliation mismatch
- Safety gate violation
- Broker API rejection spike
- SLA breach
- Kill switch activation
- Unexpected exception affecting execution flow

Then the following steps are mandatory:

1. Immediately halt trading
2. Snapshot internal state
3. Snapshot broker state
4. Record IncidentReport with timestamp and context
5. Require manual review before restart

Automatic recovery is not permitted for critical incidents.

---

## Kill Switch Test Cadence

The kill switch must be manually tested at least once during each validation window.

Test requirements:

- Trigger kill switch during active session
- Confirm open orders are canceled
- Confirm no new OrderIntent objects are created
- Confirm strategy transitions to IDLE
- Confirm system logs EmergencyEvent

Failure of the kill switch test invalidates the validation window.

---

## Manual Intervention Policy

Allowed manual actions:

- Cancel open broker orders
- Flatten positions
- Restart application process

Prohibited actions:

- Editing database state
- Modifying order logs
- Backfilling missing data without record
- Deleting audit artifacts

All manual actions must be logged in ManualInterventionLog with:

- Timestamp
- Operator identity
- Reason
- Actions taken

---

## Broker Connectivity Monitoring

The system must detect:

- API timeout exceeding configured threshold
- Five consecutive request failures
- Websocket disconnection
- Unexpected broker response codes

Response behavior:

- Pause new OrderIntent generation
- Continue reconciliation checks
- Resume only after broker health restored
- Log BrokerHealthEvent
