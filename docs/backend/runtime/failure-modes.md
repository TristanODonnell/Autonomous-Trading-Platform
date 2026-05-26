# Failure Modes

## Overview

This document describes the main failure modes currently relevant to the platform’s runtime flows and how the system behaves when they occur.

It focuses on actual implemented behavior first, while also calling out the intended behavior where the implementation is still incomplete.

---

## Ingestion Readiness Miss

### Intended Behavior

Earlier runtime and scheduler docs expected ingestion readiness to support skip, degrade, or halt behavior based on completeness and timing.

### Current Behavior

The current trading cycle only performs a deadline-based readiness check:
- before deadline → ready
- after deadline → `safe_mode=True`, reason `ingestion_deadline_missed`

If readiness fails:
- the cycle may skip evaluation when `skip_evaluation_on_ingestion_failure` is enabled
- otherwise it raises an error

Current limitations:
- no symbol-level skip
- no carry-forward data behavior
- no validation that required bars, universe snapshot, or corporate actions are actually present

---

## Order Submission Errors

### Current Behavior

During order submission:
- successful broker submission transitions an order from `NEW` to `SUBMITTED`
- submission exceptions transition the order to `REJECTED`

Retry behavior exists in the execution service for `httpx.HTTPError`, using exponential backoff.

Current limitations:
- retries are broader than the earlier design intended
- strategy state is not fully updated on broker rejection
- no full freeze hook is triggered on invalid execution semantics

---

## Invalid State Transitions

### Current Behavior

Both order and strategy state machines raise exceptions on invalid transitions.

However:
- invalid transitions do not currently trigger full freeze behavior
- required events like `ORDER_TRANSITION_INVALID` and `STRATEGY_TRANSITION_INVALID` are not emitted
- freeze state is not enforced operationally

So invalid transitions are detected, but the failure response is incomplete.

---

## Reconciliation Mismatch

### Intended Behavior

The original design expected reconciliation mismatches to:
- freeze trading
- cancel open orders
- emit reconciliation failure events
- require human acknowledgment before resuming

### Current Behavior

Current reconciliation is limited to tracked orders:
- it updates order statuses
- extracts new fills
- updates cash and position state

It does not:
- compare full positions vs broker
- compare cash/buying power comprehensively
- freeze trading on mismatch
- emit `RECONCILIATION_STARTED/PASSED/FAILED`
- require human acknowledgment

So full reconciliation failure handling is largely deferred.

---

## Freeze Conditions

### Intended Behavior

Freeze should occur for:
- reconciliation mismatch
- ingestion SLA breach
- broker connectivity instability
- kill switch activation
- invalid state transitions

### Current Behavior

Freeze handling is currently stubbed:
- `freeze_trading()` prints a message
- `is_trading_frozen()` always returns `False`

As a result:
- freeze does not persist
- open orders are not canceled automatically
- new intents are not blocked
- human acknowledgment is not implemented

This is one of the most important implementation gaps across the runtime path.

---

## Kill Switch and Runtime Gates

### Current Behavior

A kill switch service and runtime gate service exist, and the live trading gate can check them.

However:
- kill switch state is in-memory
- runtime gate state is in-memory
- order submission does not consistently invoke live gate checks
- paper runs do not receive the same enforcement path

This means safety-gate failure handling exists structurally but is not yet fully enforced at the actual execution boundary.

---

## Airflow and Scheduler Failure Handling

Current Airflow callbacks:
- print incidents
- do not persist structured failure artifacts
- do not emit the full scheduler event sequence

This means scheduler failures are visible in logs but not yet captured as a robust runtime incident stream.

---

## Operator Response Expectations

The older paper-mode operational playbook is still useful as the intended manual response model.

For critical incidents, it expects:
1. halt trading
2. snapshot internal state
3. snapshot broker state
4. record an incident report
5. require manual review before restart

It also prohibits direct DB edits and expects manual actions to be logged. That is useful as an operational target even though the automated runtime enforcement is not fully implemented yet.

---

## Current Summary

The most important current failure-mode reality is:

- failures are often detected
- some degraded paths exist
- but the system does not yet enforce the full freeze / acknowledgment / cancellation model described in the earlier runtime plans

In other words, many failure conditions are recognized, but the response path is still partial or stubbed.
