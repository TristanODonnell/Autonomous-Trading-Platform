# Safety Invariants

1. Paper and live environments are physically and logically isolated.

2. Live trading requires:
   - Build-time enablement
   - Config override
   - Runtime human token
   - External kill-switch inactive

3. No single service-layer bug can route paper to live.

4. Kill switch must exist outside:
   - Database
   - Primary execution service

5. All caps enforced before broker adapter call.

6. OrderIntent must pass:
   - Idempotency validation
   - Cap validation
   - Allowlist validation
   - Environment validation

7. Shadow mode must never initialize broker adapter.
