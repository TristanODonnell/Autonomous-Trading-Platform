# Admin CLI Domain Audit

Target CLI domain: `admin`
Target CLI file: `src/autonomous_trading_platform/cli/commands/admin.py`

## Status: COMPLETE

All sections implemented and tested. 54 tests passing in `tests/cli/commands/test_admin.py`.

---

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `admin inspect-config` | `--json` | `handle_show_config` | no | no | yes | `READ_ONLY_SAFE` |
| `admin inspect-env` | `--json` | `handle_show_env` | no | no | yes | `READ_ONLY_SAFE` |
| `admin validate-config` | `--include-broker` | `handle_validate_config` | no | no (broker opt-in) | yes | `READ_ONLY_SAFE` |
| `admin inspect-failed-runs` | `--limit` (1–1000, default 25) | `handle_list_failed_runs` | no | no | yes | `READ_ONLY_SAFE` |
| `admin inspect-failed-run` | `--run-id` (required) | `handle_inspect_failed_run` | no | no | yes | `READ_ONLY_SAFE` |
| `admin inspect-audit-log` | `--action-type`, `--strategy-id`, `--user`, `--from`, `--to`, `--page`, `--page-size` | `handle_inspect_audit_log` | no | no | yes | `READ_ONLY_SAFE` |
| `admin inspect-db` | none | `handle_inspect_db` | no | no | yes | `READ_ONLY_SAFE` |
| `admin doctor` | `--include-broker` | `handle_doctor` | no | no (broker opt-in) | yes | `READ_ONLY_SAFE` |

Notes:

- All commands now use `get_session()` directly — no broker or trading-cycle dependencies.
- `inspect-failed-runs` broker coupling removed (was using `build_trading_cycle_dependencies()`).
- `--limit` validation enforces 1–1000 range with clear error output.
- `--include-broker` on `validate-config` and `doctor` makes broker credential checks opt-in.
- Alembic version table check in `inspect-db` and `doctor` is advisory (does not fail the command if the table is absent, e.g. fresh/test DB).

---

## 2. Domain Responsibility Check

| Command | Classification | Rationale |
|---|---|---|
| `admin inspect-config` | correctly placed | Config inspection is administrative. |
| `admin inspect-env` | correctly placed | Environment inspection is administrative. |
| `admin validate-config` | correctly placed | Config validation is administrative. |
| `admin inspect-failed-runs` | correctly placed | Refactored; broker coupling removed. |
| `admin inspect-failed-run` | correctly placed | Single-run failure triage is administrative. |
| `admin inspect-audit-log` | correctly placed | Audit log is administrative evidence. |
| `admin inspect-db` | correctly placed | Database connectivity is an admin preflight. |
| `admin doctor` | correctly placed | Bundled admin preflight. |

**Runtime wrapper added:** `runtime list-failed-runs` added to `runtime.py` as a runtime-native alias (Section 2 recommendation). No broker dependency.

---

## 3. Missing CLI Coverage

All P0 and P1 items implemented. P2 items (`admin export-failure-bundle`) remain deferred.

| Proposed command path | Priority | Status |
|---|---|---|
| `admin validate-config` | P0 | ✅ Implemented |
| `admin inspect-config --json` | P1 | ✅ Implemented |
| `admin inspect-env --json` | P1 | ✅ Implemented |
| `admin inspect-failed-run --run-id <uuid>` | P0 | ✅ Implemented |
| `admin inspect-audit-log` | P1 | ✅ Implemented |
| `admin inspect-db` | P1 | ✅ Implemented |
| `admin doctor` | P2 | ✅ Implemented (pulled forward from P2) |
| `admin export-failure-bundle --run-id <uuid> --output <dir>` | P2 | ⏳ Deferred |

---

## 4. Testing Plan

### Phase 0: `--help` smoke tests

```powershell
atp admin --help
atp admin inspect-config --help
atp admin inspect-env --help
atp admin validate-config --help
atp admin inspect-failed-runs --help
atp admin inspect-failed-run --help
atp admin inspect-audit-log --help
atp admin inspect-db --help
atp admin doctor --help
```

### Phase 1: safe read-only commands (no DB)

These use the local `.env` / environment as-is:

```powershell
atp admin inspect-config
atp admin inspect-config --json
atp admin inspect-env
atp admin inspect-env --json
atp admin validate-config
atp admin validate-config --include-broker
```

With minimal test-safe overrides (skips broker validation):

```powershell
$env:APP_ENV="test"
$env:DATABASE_URL="sqlite:///:memory:"
$env:TRADING_ENVIRONMENT="paper"
$env:NO_LIVE_TRADING="true"
$env:VALIDATE_BROKER_CONFIG="false"
atp admin inspect-config
atp admin inspect-env
atp admin validate-config
```

### Phase 2: DB-dependent read-only commands (requires running Postgres or real DB)

```powershell
# With Docker Postgres running (port 5433):
atp admin inspect-db
atp admin inspect-failed-runs
atp admin inspect-failed-runs --limit 5
atp admin inspect-failed-runs --limit 0        # should error: limit out of range
atp admin inspect-failed-runs --limit 9999     # should error: limit out of range
atp admin inspect-audit-log
atp admin inspect-audit-log --action-type KILL_SWITCH_ACTIVATED
atp admin inspect-audit-log --from 2026-01-01T00:00:00+00:00 --to 2026-06-01T00:00:00+00:00
atp admin inspect-audit-log --page 1 --page-size 10
atp admin doctor
atp admin doctor --include-broker
```

### Phase 3: single-run detail (requires a known failed run_id from DB)

```powershell
# Replace <uuid> with a real run_id from `admin inspect-failed-runs` output:
atp admin inspect-failed-run --run-id <uuid>
atp admin inspect-failed-run --run-id not-a-uuid       # should error: invalid UUID
atp admin inspect-failed-run --run-id 00000000-0000-0000-0000-000000000000  # should error: not found
```

### Phase 4: broker credential validation (requires real paper API keys)

```powershell
$env:TRADING_ENVIRONMENT="paper"
$env:PAPER_BROKER_API_KEY="<paper-key>"
$env:PAPER_BROKER_API_SECRET="<paper-secret>"
$env:VALIDATE_BROKER_CONFIG="false"   # Settings instantiation
atp admin validate-config --include-broker
atp admin doctor --include-broker
```

> Note: `inspect-failed-runs` and all other DB commands no longer require broker credentials.
> Phase 4 is now only relevant for `validate-config --include-broker` and `doctor --include-broker`.

### Runtime wrapper (cross-domain)

```powershell
atp runtime list-failed-runs
atp runtime list-failed-runs --limit 5
```

---

## 5. Risks / Suspicious Wiring

All risks from the original audit have been addressed:

| Risk | Status |
|---|---|
| `inspect-failed-runs` broker-coupled via `build_trading_cycle_dependencies()` | ✅ Fixed: now uses `get_session()` directly |
| `inspect-failed-runs --limit` has no lower/upper validation | ✅ Fixed: 1–1000 enforced |
| Parser help says `"Admin cycle operations"` (misleading) | ✅ Fixed: updated to `"Admin configuration, inspection, and failure-triage utilities"` |
| `inspect-config` redacts a fixed denylist (future leak risk) | ⚠️ Partial: denylist still used; documented in `_REDACTED_KEYS` constant. Allowlist/metadata-based approach deferred. |
| `inspect-config` and `inspect-env` JSON output inconsistent | ✅ Fixed: both support `--json` flag |
| No command emits artifacts | ⏳ `export-failure-bundle` deferred (P2) |
| Broker checks not opt-in for future `doctor`-style commands | ✅ Fixed: `--include-broker` flag on `validate-config` and `doctor` |

---

## 6. Recommended Refactor / Extension

All recommended changes implemented:

- `inspect-failed-runs` refactored to `get_session()` only — no broker dependency.
- `--limit` validation (1–1000) added.
- `admin validate-config` added (P0).
- `admin inspect-failed-run --run-id` added (P0).
- `admin inspect-audit-log` with full filter set added (P1).
- `admin inspect-db` added (P1).
- `admin doctor` added (P2 pulled forward).
- `--json` flag added to `inspect-config` and `inspect-env` (P1).
- `runtime list-failed-runs` added as runtime-native alias (Section 2).
- Parser description corrected from `"Admin cycle operations"`.

---

## 7. Final Summary Table

| Command | Status | Correct Domain? | Risk | State |
|---|---|---:|---|---|
| `admin inspect-config` | Works; `--json` added; secrets redacted | yes | Low: denylist-based redaction | Done |
| `admin inspect-env` | Works; `--json` added; full credential coverage | yes | Low | Done |
| `admin validate-config` | New; structured pass/fail; broker opt-in | yes | Low | Done |
| `admin inspect-failed-runs` | Refactored; broker coupling removed; limit validated | yes | Low | Done |
| `admin inspect-failed-run` | New; single-run detail; UUID validation | yes | Low | Done |
| `admin inspect-audit-log` | New; full filter set; ISO 8601 date validation | yes | Low | Done |
| `admin inspect-db` | New; connectivity + advisory alembic check | yes | Low | Done |
| `admin doctor` | New; bundled preflight; broker opt-in | yes | Low | Done |
| `runtime list-failed-runs` | New; runtime-native alias; no broker dependency | yes | Low | Done |
