# Environment Model Specification

## Objective
Define strict separation between paper and live trading environments.

This phase enforces capital isolation before execution logic exists.

---

## Environment Types

Two mutually exclusive environments:

- paper
- live

No hybrid mode permitted.

---

## Namespace Isolation

Each environment must have:

- Separate configuration namespace
- Separate credential store
- Separate broker account_id
- Separate database schema or logical partition
- Separate audit log channel

Cross-environment credential reuse is prohibited.

---

## Paper-Only Build (v1 Default)

v1 execution layer must support a "paper-only build" path:

- Live broker adapter not included in build artifact
- Live configuration flags not compiled
- Live credential loading disabled

This guarantees v1 cannot route live orders even if config bug exists.

---

## RunManifest Requirement

RunManifest must include:

environment: paper | live
broker_account_id: <explicit>

Mismatch between environment and account_id must hard-fail pre-execution.
