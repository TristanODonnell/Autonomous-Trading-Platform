# Broker Account Allowlist Specification

## Objective
Prevent orders from being routed to an unintended broker account.

The system must only submit orders to explicitly allowlisted broker accounts,
and this check must occur pre-execution.

---

## Definitions

### account_id
The broker account identifier targeted by an OrderIntent.

### allowlist
A static set of permitted account IDs for the current environment.

---

## Core Rule

No broker submission is permitted unless:

OrderIntent.account_id ∈ Allowlist(environment)

If not allowlisted → hard fail + audit log.

---

## Environment-Specific Allowlists

Allowlist is environment-scoped:

### paper
ALLOWLIST_PAPER = { <paper_account_id_1>, ... }

### live
ALLOWLIST_LIVE = { <live_account_id_1>, ... }

### shadow
ALLOWLIST_SHADOW = ∅
(no broker calls allowed; allowlist unused but must exist conceptually)

No account_id may appear in both allowlists unless explicitly justified.

---

## Where Allowlist Lives (Safety Requirement)

Allowlist must not be editable at runtime via the primary database.

It must live in at least one source outside the DB, e.g.:

- environment config file checked into repo (for paper)
- secrets manager / encrypted config (for live)
- infrastructure-managed environment variable

Rationale:
prevents a DB corruption/compromise from enabling live routing.

---

## OrderIntent Contract Requirements

OrderIntent must include:
- account_id
- environment (or inherit from RunManifest environment)
- idempotency_key

The execution layer must validate:
1. RunManifest.environment matches runtime environment
2. OrderIntent.account_id is allowlisted for that environment
3. OrderIntent.environment (if present) matches RunManifest.environment

Mismatch → hard fail.

---

## Reconciliation Constraint

BrokerAdapter must be bound to exactly one account_id per run.

It is invalid to:
- submit orders to multiple accounts within a single run_id
- switch accounts mid-run

This makes auditing and replay deterministic.

---

## Failure Modes

### Config points to wrong account_id
Allowlist blocks it.

### Strategy accidentally emits an OrderIntent with live account_id during paper run
Allowlist blocks it (and logs incident).

### DB mutation tries to change account_id
Allowlist is outside DB, so it still blocks.

---

## Audit Logging Requirements

On every broker submission attempt:

Log:
- run_id
- environment
- intended account_id
- allowlist match true/false
- rejection reason if false

---

## Acceptance Tests (Planning-Level)

1. Paper run with live account_id in OrderIntent:
   - Must hard fail before any broker call
   - Must log allowlist violation

2. Live run with account_id not in ALLOWLIST_LIVE:
   - Must hard fail
   - Must not initialize broker client

3. Shadow run:
   - Must never consult broker allowlist for execution because broker is disabled
