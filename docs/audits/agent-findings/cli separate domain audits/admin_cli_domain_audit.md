# Admin CLI Domain Audit

Target CLI domain: `admin`
Target CLI file: `src/autonomous_trading_platform/cli/commands/admin.py`

## 1. Current CLI Inventory

| Command path | Arguments/options | Handler | Mutates state? | Calls external APIs? | Safe for local read-only testing? | Phase classification |
|---|---|---|---:|---:|---:|---|
| `admin inspect-config` | none | `handle_show_config` | no | no | conditional | `READ_ONLY_SAFE` |
| `admin inspect-env` | none | `handle_show_env` | no | no | conditional | `READ_ONLY_SAFE` |
| `admin inspect-failed-runs` | `--limit`, default `25` | `handle_list_failed_runs` | no intended DB mutation; opens/commits UOW transaction | conditional/likely yes | no | `BROKER_OR_EXTERNAL` |

Notes:

- `inspect-config` and `inspect-env` instantiate `Settings()`, so they require the environment to be valid before output is produced.
- `inspect-failed-runs` should be DB read-only, but it calls `build_trading_cycle_dependencies()`, which builds execution context and performs broker startup readiness through `AlpacaBrokerClient`.

## 2. Domain Responsibility Check

| Command | Classification | Rationale |
|---|---|---|
| `admin inspect-config` | correctly placed | Config inspection is administrative. |
| `admin inspect-env` | correctly placed | Environment inspection is administrative. |
| `admin inspect-failed-runs` | should be duplicated/wrapped elsewhere | Failure triage fits admin, but run manifests are runtime-owned. Add a runtime-native `runtime list-failed-runs` or `runtime inspect-failures`; keep admin as a convenience wrapper. |

## 3. Missing CLI Coverage

| Proposed command path | Purpose | Why it belongs in this domain | Classification | Implementation target | Priority |
|---|---|---|---|---|---|
| `admin validate-config` | Validate required env/config and print structured pass/fail diagnostics. | Administrative config inspection. | read-only | `Settings`, `broker_config_validator` | P0 |
| `admin inspect-config --format json` or `--json` | Machine-readable config output without header noise. | Makes admin checks scriptable. | read-only | existing `handle_show_config` | P1 |
| `admin inspect-env --format json` or `--json` | Machine-readable env presence report. | Makes admin checks scriptable. | read-only | existing `handle_show_env` | P1 |
| `admin inspect-failed-run --run-id <uuid>` | Show one-run failure detail: manifest fields, failed step, error, linked audit events. | Admin failure triage. | read-only / cross-domain wrapper | `RunManifestRepository.get_by_run_id`, `AuditLogService.list_events` | P0 |
| `admin inspect-audit-log` | List audit events with filters for action type, user, strategy, and date range. | Audit log is administrative evidence. | read-only | `AuditLogService.list_events`, `/api/v1/audit-log` parity | P1 |
| `admin inspect-db` | Verify DB URL presence, connectivity, and current schema visibility. | Administrative environment/database utility. | read-only | `get_session`, SQLAlchemy inspection, optional Alembic version table | P1 |
| `admin doctor` | Bundled local preflight: config, env, DB, redaction, optional broker check. | Administrative setup and failure utility. | read-only by default; broker-facing with `--include-broker` | `Settings`, DB session, optional broker health | P2 |
| `admin export-failure-bundle --run-id <uuid> --output <dir>` | Emit manifest, audit log, config/env summary, and relevant runtime state as artifacts. | Admin failure handoff/debugging. | platform-level artifact output | `RunManifestRepository`, `AuditLogService`, runtime snapshot services | P2 |

## 4. Testing Plan

Phase 0: `--help` commands

```powershell
atp admin --help
atp admin inspect-config --help
atp admin inspect-env --help
atp admin inspect-failed-runs --help
```

Phase 1: safe read-only commands

```powershell
atp admin inspect-config
atp admin inspect-env
```

With test env placeholders:

```powershell
$env:APP_ENV="test"
$env:DATABASE_URL="sqlite:///:memory:"
$env:TRADING_ENVIRONMENT="paper"
$env:NO_LIVE_TRADING="true"
$env:VALIDATE_BROKER_CONFIG="false"
atp admin inspect-config
atp admin inspect-env
```

Phase 2: local DB mutation commands

```powershell
# None currently registered.
```

Phase 3: cross-domain/runtime commands

```powershell
$env:VALIDATE_BROKER_CONFIG="false"
atp admin inspect-failed-runs --limit 10
```

Phase 4: broker/external commands if applicable

```powershell
$env:TRADING_ENVIRONMENT="paper"
$env:PAPER_BROKER_API_KEY="<paper-key>"
$env:PAPER_BROKER_API_SECRET="<paper-secret>"
$env:VALIDATE_BROKER_CONFIG="true"
atp admin inspect-failed-runs --limit 10
```

This Phase 4 requirement is undesirable for `inspect-failed-runs`; it should not need broker access.

## 5. Risks / Suspicious Wiring

- No handler signature mismatches found.
- No parser-required handler inputs are missing.
- No obvious placeholder commands.
- `admin` parser help says `"Admin cycle operations"`, which is misleading because config/env inspection is broader than cycles.
- `inspect-failed-runs` is suspicious: it uses `build_trading_cycle_dependencies()` just to get a DB session, which constructs execution dependencies and triggers broker startup health checks.
- `inspect-failed-runs` can fail because broker credentials/network are unavailable before it ever lists local failed runs.
- `inspect-failed-runs --limit` has no lower/upper validation; negative or excessive limits are not guarded.
- `inspect-config` redacts a fixed denylist. Future sensitive fields added to `Settings.__dict__` could leak unless redaction switches to an allowlist or metadata-based sensitivity.
- `inspect-config` and `inspect-env` do not support JSON-only output mode consistently; `inspect-config` emits a header plus JSON, while `inspect-env` emits key-value rows.
- No command emits artifacts, even though failure inspection often needs a portable bundle.
- No `--dry-run` is needed for current read-only commands, but future `doctor` or export-style commands should make broker checks opt-in.

## 6. Recommended Refactor / Extension

Do not split the domain. Keep `inspect-config` and `inspect-env`, but add structured output options and safer redaction.

Refactor `inspect-failed-runs` to build only `Settings()` plus `get_session()` or a small admin DB dependency, not full trading-cycle dependencies. Add `--limit` validation. Consider duplicating it as `runtime list-failed-runs` while keeping `admin inspect-failed-runs` as a wrapper.

Add P0 commands: `admin validate-config` and `admin inspect-failed-run --run-id`. Add P1 commands: `admin inspect-audit-log`, `admin inspect-db`, and JSON output flags.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `admin inspect-config` | Works as read-only config dump | yes | Medium: fixed secret denylist | Keep; add allowlist/redaction policy and JSON mode |
| `admin inspect-env` | Works as read-only env presence report | yes | Low: requires valid `Settings()` | Keep; add JSON mode and broader safety flags |
| `admin inspect-failed-runs` | Intended read-only DB query but broker-coupled | partial | High: external broker check during local failure inspection | Refactor dependency wiring; add runtime equivalent and limit validation |
