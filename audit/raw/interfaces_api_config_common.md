# Audit: interfaces/ + api/ + config/ + common/

Audited: 2026-07-07. Branch `main` @ 756e319eb.

## Verified counts

File counts (all files, `find <dir> -type f | wc -l`):
- `src/autonomous_trading_platform/interfaces/` — **33 files** (all .py: 1 pkg init + rest/: app.py, 2 inits, 15 route files, 15 schema files)
- `src/autonomous_trading_platform/api/` — **9 files** (all .py)
- `src/autonomous_trading_platform/config/` — 7 files total, of which **4 .py** (3 are `__pycache__/*.pyc`)
- `src/autonomous_trading_platform/common/` — 6 files total, of which **4 .py** (2 are `__pycache__/*.pyc`)

LOC (`wc -l` on .py files):
- interfaces/: **4,981** (routes/ subtotal 3,762; schemas/ subtotal 1,116; app.py 100)
- api/: **371**
- config/: **307**
- common/: **75**
- Combined: **5,734**

Route handlers:
- `grep -rE '@router\.(get|post|put|patch|delete)' src/autonomous_trading_platform/interfaces/rest/ | wc -l` → **90** (63 GET, 21 POST, 4 PUT, 2 PATCH)
- Broadened to `@[a-z_]*router\.` (catches `@experiments_router.*` in strategies_routes.py) → **95** decorated handlers
- Plus 1 unauthenticated `GET /health` defined inline in `app.py` → **96 endpoints total** on the app

TODO/FIXME/XXX across all four dirs: **0** (`grep -rnE 'TODO|FIXME|XXX' ... | wc -l` → 0)

## Special-attention claims (verified/refuted)

1. **Middleware order — VERIFIED.** `interfaces/rest/app.py` lines 63-68: comment states `RequestID → Logging → JWT → Deprecation → route handler`, and `add_middleware` calls are in correct reverse order (Deprecation added first = innermost, RequestID added last = outermost). CORS is added after RequestID so CORS is actually the true outermost layer (fine; comment doesn't mention it).
2. **JWT closed role set at auth layer — VERIFIED.** `api/auth_middleware.py` line 11: `VALID_ROLES = {"operator", "researcher", "risk_manager", "admin"}`. Any authenticated request whose token lacks a role in this set gets 403 at the middleware, before any route runs. Additionally, per-route RBAC exists as FastAPI dependencies in `api/dependencies.py` (`require_operator_or_admin`, `require_risk_manager_or_admin`, `require_admin`) — so it's two-tier: closed set at middleware + finer-grained role gates per mutating route. The "rather than in route handlers" phrasing is only half-true: role *validity* is middleware-enforced, but role *authorization* for sensitive routes is dependency-enforced.
3. **One route file per API domain — VERIFIED, but the CLAUDE.md list is stale/incomplete.** Actual route files (15, each with its own `APIRouter` prefix): activity (`/activity`), audit_log (`/audit-log`), control (`/controls`), drawdown_governance (`/drawdown-governance`), governance_audit (`/governance-audit`), metadata (`/metadata`), metrics (`/metrics`), operations (`/operations`), portfolio_construction (`/portfolio/construction`), portfolio (`/portfolio`), settings (`/settings`), shadow (`/shadow`), strategies (`/strategies` + second `/experiments` router in same file), system (`/system`). All mounted under `/api/v1`. There is no separate `audit_log`-only claim issue; but CLAUDE.md's list of six domains omits nine other route files.
4. **`api/` vs `interfaces/` — NOT dead code, NOT duplication.** `src/autonomous_trading_platform/api/` is the cross-cutting HTTP infrastructure package (middleware, response envelope, error taxonomy, exception handlers, auth dependencies) consumed by `interfaces/rest/` (app.py imports all four middlewares + `register_exception_handlers` from it; every route file imports `success_response`/`SuccessEnvelope`/`get_request_id` from it). Architecturally it arguably belongs *inside* interfaces/ (it is presentation-layer code living as a sibling top-level package), but it is live, load-bearing code.
5. **CLI entry points under interfaces/ — NONE.** `interfaces/` contains only `rest/`. CLAUDE.md's claim "interfaces/ ← REST API (FastAPI), CLI" is inaccurate for this tree: no CLI module exists under interfaces/ (CLI-ish scripts live in top-level `scripts/`, outside audit scope).

---

## Per-file entries

### src/autonomous_trading_platform/api/__init__.py (32 lines)
- Purpose: Public surface of the API infra package; re-exports envelope models, errors, middlewares, `register_exception_handlers`, `get_request_id`, `deprecated`.
- Notable: Curated `__all__` (16 names) — deliberate façade pattern.

### src/autonomous_trading_platform/api/auth_middleware.py (62 lines)
- Purpose: `JWTAuthMiddleware` (Starlette `BaseHTTPMiddleware`): validates Bearer JWT (PyJWT, HS256 default), enforces closed role set, stashes `user_id`/`role` on `request.state`.
- Notable: `VALID_ROLES = {"operator", "researcher", "risk_manager", "admin"}`; 401 for missing/expired/invalid token, 403 for missing/unknown role. PUBLIC_PATHS allowlist: `/health`, `/docs`, `/openapi.json`, `/redoc`. Smells: (a) `JWT_SECRET = os.environ["JWT_SECRET"]` at **import time** — importing this module without the env var set raises KeyError (hard fail-closed, but couples import to environment and bypasses `config.Settings`); (b) `startswith` prefix matching on PUBLIC_PATHS means e.g. `/healthanything` is also public (minor); (c) secret/algorithm read via raw `os.environ`, not the `Settings` loader.

### src/autonomous_trading_platform/api/middleware.py (18 lines)
- Purpose: `RequestIDMiddleware` — honors inbound `X-Request-ID` or mints a uuid4, sets `request.state.request_id`, echoes header on response.

### src/autonomous_trading_platform/api/logging_middleware.py (37 lines)
- Purpose: `RequestLoggingMiddleware` — structured per-request log (`api_request`) with request_id, user_id, method, endpoint, status_code, latency_ms via `logger.info(..., extra=...)`.
- Notable: Graceful fallback if RequestID/JWT middlewares haven't populated state; comment references "EPIC-1 / Story 4 structured log fields" (traceable to planning docs).

### src/autonomous_trading_platform/api/deprecation.py (31 lines)
- Purpose: `@deprecated(sunset=date(...))` decorator marks endpoints; `DeprecationMiddleware` reads the marker off the matched route's endpoint and sets `Deprecation: true` + `Sunset: <ISO date>` response headers.
- Notable: Clean route-scope introspection (`request.scope.get("route")`). Note: `grep -rn '@deprecated' src/` shows no route currently uses it — machinery ready but unused.

### src/autonomous_trading_platform/api/envelope.py (61 lines)
- Purpose: Uniform response envelope: `SuccessEnvelope[T]` (generic Pydantic) / `ErrorEnvelope` with `ResponseMeta{request_id, timestamp, version}`; `success_response()` / `error_response()` builders. `API_VERSION = "v1"`.
- Notable: Every route in interfaces/rest returns `SuccessEnvelope[...]` — consistently applied contract, good for FE codegen.

### src/autonomous_trading_platform/api/errors.py (30 lines)
- Purpose: `ErrorCode` StrEnum (9 codes incl. domain-specific `ACTION_BLOCKED`) and `APIError` exception carrying code/message/http_status/details.

### src/autonomous_trading_platform/api/exception_handlers.py (43 lines)
- Purpose: Registers handlers translating `APIError` and FastAPI `RequestValidationError` (→422 `VALIDATION_ERROR`) into the `ErrorEnvelope` shape; request_id resolution falls back to inbound header for tests.

### src/autonomous_trading_platform/api/dependencies.py (57 lines)
- Purpose: FastAPI dependencies: `get_settings()` (lru_cache singleton), `get_alpaca_broker_client()` (returns `None` on any construction failure), `get_request_id`, and three RBAC gates (`require_operator_or_admin`, `require_risk_manager_or_admin`, `require_admin`) returning the acting user_id for audit attribution.
- Notable: RBAC gates read `request.state.role` set by the middleware — clean layering. Smell: `get_alpaca_broker_client` swallows all exceptions (`except Exception: return None`).

### src/autonomous_trading_platform/config/__init__.py (0 lines)
- Purpose: Package marker.

### src/autonomous_trading_platform/config/enums.py (6 lines)
- Purpose: `TradingEnvironment` StrEnum: `PAPER`/`LIVE`.

### src/autonomous_trading_platform/config/settings.py (244 lines)
- Purpose: Env-based `Settings` loader (plain class + `python-dotenv`, not pydantic-settings) with ~50 typed knobs: env identity, DB URL, trading env, safety flags (`NO_LIVE_TRADING` default True, `ENABLE_LIVE_TRADING` default False), broker creds split per environment (paper vs live key pairs), risk limits (gross/net/symbol/sector exposure, leverage, order rate limits), timeouts/SLAs, paper-runtime order safeguards (max qty/notional/limit price, allowed symbols, require-limit-orders), failure policies (skip-eval-on-ingestion-failure, hold-on-eval-failure, freeze-on-reconciliation-failure).
- Notable: Fail-safe defaults throughout (safety flags default to safest value). Validates broker env config at construction via `broker_config_validator`. `alpaca_base_url` is a *derived* property (cannot be overridden by env) — env can't point paper mode at the live URL. **Bug/smell: `trading_cycle_timeout_seconds` assigned twice** (lines 132-135 default 240, then lines 140-143 default 300 — second wins; dead first assignment and ambiguous intended default). Minor: `Decimal`/`int`/`float` parsers raise raw ValueError on malformed env values instead of a labeled error.

### src/autonomous_trading_platform/config/broker_config_validator.py (57 lines)
- Purpose: Environment-isolation validators: required broker creds must exist for the active environment; Alpaca base URL must exactly match the canonical paper/live URL for the environment.
- Notable: Part of the safety doctrine (paper/live isolation enforced at config load, not just at runtime gates).

### src/autonomous_trading_platform/common/__init__.py (0 lines)
- Purpose: Package marker.

### src/autonomous_trading_platform/common/annualisation.py (24 lines)
- Purpose: Shared annualisation constants: `BARS_PER_DAY=78` (5-min bars), `TRADING_DAYS_PER_YEAR=252`, `BARS_PER_YEAR=19_656`.
- Notable: Unusually good docstring documenting the trading-day-year convention and its ~1.45x divergence from calendar-day CAGR — deliberate, explained, and consistent across return/risk metrics.

### src/autonomous_trading_platform/common/errors.py (26 lines)
- Purpose: Infrastructure error taxonomy: `InfrastructureError` → `TransientInfrastructureError` (Broker unavailable, DB connection, external timeout) vs `PersistentInfrastructureError` (misconfiguration).
- Notable: Transient/persistent split designed for retry-policy branching.

### src/autonomous_trading_platform/common/system_info.py (25 lines)
- Purpose: Provenance helpers: `get_git_commit()` (subprocess, "unknown" fallback) and `get_dependency_lock_hash()` (sha256 of poetry.lock/requirements.txt/Pipfile.lock).
- Notable: Used for research-run reproducibility stamping. `get_dependency_lock_hash` resolves lockfile relative to CWD, not repo root — fragile if invoked from elsewhere.

### src/autonomous_trading_platform/interfaces/__init__.py (0 lines) / interfaces/rest/__init__.py (3 lines) / rest/routes/__init__.py (0) / rest/schemas/__init__.py (0)
- Purpose: Package markers; `rest/__init__.py` re-exports `create_app`.

### src/autonomous_trading_platform/interfaces/rest/app.py (100 lines)
- Purpose: FastAPI app factory `create_app()`: registers 4 custom middlewares + CORS, exception handlers, 16 routers (15 files, strategies contributes 2) all under `/api/v1`, plus inline public `GET /health`.
- Notable: Middleware order comment + reverse `add_middleware` ordering correct (see claims section). CORS locked to `http://localhost:5173` (Vite dev origin). No lifespan/startup hooks, no DB init here — sessions come per-request from `db.get_session` dependency.

### src/autonomous_trading_platform/interfaces/rest/routes/strategies_routes.py (854 lines)
- Purpose: Largest route module: strategy catalog (list/detail/equity-curve/compare), active strategies, allocations (list + PUT override with reason), enable/disable, governance transitions, experiments CRUD-ish (`/experiments` second router: create/list/cancel/detail/strategies), strategy health + health lifecycle endpoints (list, per-strategy, transitions, allocation-penalty, operator clear-suspension). 18 handlers (13 on `/strategies`, 5 on `/experiments`).
- Notable: (a) Rich domain-exception → HTTP mapping: `MissingSourceRunError`→422, `PromotionRulesMissingError`/`PromotionCriteriaConfigurationError`→409 with actionable operator messages; `AllocationBudgetExceededError`→409 `APIError` with structured details. (b) Mutations require RBAC deps and a non-empty `reason`, actor recorded for audit. (c) Governance transition enforces `source_run_id` for capital-bearing promotions (evidence-linked promotion). (d) **Route-shadowing bug risk: `GET /strategies/{strategy_id}` is registered (line 173) *before* `GET /strategies/health` (line 565) and `GET /strategies/health/lifecycle`** — FastAPI matches in registration order, so `/strategies/health` resolves to `get_strategy_detail(strategy_id="health")` (likely 404 from service), and `/strategies/health/lifecycle` is safe only because it has two segments. `/active` and `/allocations` are correctly registered before the param route. (e) Health endpoints construct repositories directly in the route (bypasses service layer — inconsistent with the rest of the file). (f) `GET /{strategy_id}/health` lazily runs a full health evaluation on cache miss — a GET with side effects.

### src/autonomous_trading_platform/interfaces/rest/routes/activity_routes.py (37 lines)
- Purpose: Single `GET /activity/recent` endpoint returning the N most recent activity-feed items (dashboard widget) via `RecentActivityService`.
- Notable: No RBAC dependency — any authenticated role can read (reasonable for a read-only dashboard feed).

### src/autonomous_trading_platform/interfaces/rest/routes/audit_log_routes.py (76 lines)
- Purpose: Single `GET /audit-log` endpoint: paginated, multi-filter (action_type/strategy_id/user_id/date range) query over the audit log via `AuditLogService`.
- Notable: Gated by `require_operator_or_admin`. Clean pagination envelope (`AuditLogPaginationResponse`).

### src/autonomous_trading_platform/interfaces/rest/routes/control_routes.py (166 lines)
- Purpose: `GET /controls/state` (aggregate kill-switch/trading/per-strategy control state) plus three mutating actions: `POST /controls/kill-switch`, `/pause`, `/resume`, all via `RuntimeControlService`.
- Notable: All three mutations gated by `require_operator_or_admin` and hand-roll a `422` if `reason`/`rationale` is blank after `.strip()` — duplicated validation logic that could live in the Pydantic schema (`Field(min_length=1)` already exists on `KillSwitchRequest.reason` and `RuntimeControlActionRequest.rationale`, making the manual `.strip()` check partially redundant with schema-level validation, though it does catch whitespace-only strings that pass `min_length=1`).

### src/autonomous_trading_platform/interfaces/rest/routes/drawdown_governance_routes.py (290 lines)
- Purpose: Drawdown-governance ladder state API: list all/pending-ack/config, per-strategy state + transition history, operator breach-acknowledgement, and manual `POST /run` to trigger ladder evaluation.
- Notable: (a) Defines its own response/request Pydantic models inline in the route file (`DrawdownGovernanceLadderListResponse`, `DrawdownGovernanceAckRequest`, etc.) rather than in `schemas/` — inconsistent with every other route file's convention of importing from `interfaces/rest/schemas/`. (b) `get_ladder_config` calls `svc._load_config()` — a route reaching into a service's private (underscore-prefixed) method, a layering smell. (c) Read endpoints (list/pending-ack/config/detail/transitions) gated by `require_risk_manager_or_admin`; mutating endpoints (acknowledge-breach, run) gated by `require_operator_or_admin` — an unusual split where *reads* require a stricter role than the *manual trigger* action.

### src/autonomous_trading_platform/interfaces/rest/routes/governance_audit_routes.py (266 lines)
- Purpose: Read/query API over the immutable governance-audit decision log: list (with rich filters), per-strategy list, single record, supersession-chain trace, and `POST /{id}/supersede` to mark an event amended.
- Notable: (a) Also defines response/request models inline rather than in `schemas/` (same pattern as drawdown_governance_routes.py). (b) Explicit amendment model: audit events are immutable; corrections are expressed via a new event + `superseded_by` link, never a mutation — good append-only audit design. (c) `supersede_governance_audit_event` calls `session.flush()` then `session.commit()` directly in the route (session lifecycle normally owned by the `get_session` dependency) and does a local import of `GovernanceAuditRepository` mid-function instead of a top-level import — minor code smell. (d) `actor = getattr(auth, "sub", "operator")` silently defaults to the literal string `"operator"` if the RBAC dependency doesn't expose `.sub` — `require_operator_or_admin` actually returns a plain `str` (user_id), which has no `.sub` attribute, so every call falls through to the hardcoded `"operator"` fallback rather than recording the real actor. This looks like a live bug: the actor attribution on `/supersede` is always `"operator"` regardless of who called it.

### src/autonomous_trading_platform/interfaces/rest/routes/metadata_routes.py (178 lines)
- Purpose: Ingestion-pipeline metadata write/read API: create dataset-version / feature-dataset-version / ingestion-run records, mark ingestion runs complete/failed, and fetch the latest dataset/feature version.
- Notable: **Outlier among all 14 route files** — no `get_request_id`, no `SuccessEnvelope`/`success_response`, and no RBAC dependency (`require_operator_or_admin` etc.) at all; every other route file in the tree uses all three consistently. Mutating endpoints here (`POST /dataset-versions`, `/ingestion-runs`, `/feature-dataset-versions`, `PATCH .../complete`, `.../fail`) are reachable by *any* authenticated user with a valid role (operator, researcher, risk_manager, or admin) — no finer-grained gate. Likely because this is pipeline-internal/machine-to-machine metadata registration (called by ingestion jobs, not human operators), but it's a real inconsistency versus the rest of the API's RBAC-everywhere pattern and worth a second look if these endpoints are reachable from the public API surface.

### src/autonomous_trading_platform/interfaces/rest/routes/metrics_routes.py (285 lines)
- Purpose: Metric-lineage API: per-strategy lineage summary (research vs. live vs. blended), research-only metrics, live-only metrics, and blended-metrics compute/get/history — the mechanism for the confidence-adaptive research→live metric blending used in allocation decisions.
- Notable: Well-documented docstrings explicitly answering "why is this strategy getting this allocation" — unusually strong operator-facing documentation embedded in the route layer. All endpoints gated by `require_operator_or_admin`. `compute_blended_metrics` is a `POST` that both computes *and persists* (with `session.commit()` in the route) — a write endpoint correctly modeled as POST (unlike the GET-with-side-effects pattern flagged in strategies_routes.py).

### src/autonomous_trading_platform/interfaces/rest/routes/operations_routes.py (282 lines)
- Purpose: Operational tooling API: Airflow-like job/job-run introspection (`/jobs`, `/jobs/{name}/runs`, `/runtime-state`) plus a full operational-alerts CRUD-ish surface (list/create/acknowledge/resolve/snooze/unsnooze/add-note).
- Notable: (a) All endpoints gated by `require_operator_or_admin`. (b) Repeated `except Exception as exc: raise _alert_error(exc) from exc` pattern across 6 of the 9 handlers — a broad `except Exception` catch-and-reclassify helper (`_alert_error`) that maps `LookupError`→404, `ValueError`→422, else re-raises; functionally fine (it re-raises unknown exceptions rather than swallowing them) but the repetition across handlers could be a FastAPI exception handler/dependency instead of per-call try/except.

### src/autonomous_trading_platform/interfaces/rest/routes/portfolio_construction_routes.py (251 lines)
- Purpose: Read-only introspection API (Recommendation 6.5) over the two-phase portfolio-construction pipeline's persisted artifacts: construction runs (by run_id or batch_id), raw signals, netted signals, constraint-gated intents, and detected cross-strategy conflicts.
- Notable: All handlers gated by `require_operator_or_admin`. Serializes ORM rows to `dict[str, Any]` via local `_*_to_dict` helper functions rather than typed Pydantic response schemas (response_model is `SuccessEnvelope[dict[str, Any]]`/`list[dict[str, Any]]`) — loses OpenAPI schema fidelity/FE type-safety compared to every other route file's typed responses; likely deliberate for a fast-moving diagnostics/debugging surface. Two 404-raising handlers do a local `from fastapi import HTTPException` inside the function body instead of a top-level import (repeated in 2 places).

### src/autonomous_trading_platform/interfaces/rest/routes/portfolio_routes.py (481 lines)
- Purpose: Largest read surface: portfolio summary/equity-curve/performance/holdings/allocation/risk/performance-by-period, plus factor-exposure snapshots+history+per-strategy decomposition and factor-neutralization config/current/history.
- Notable: (a) **No RBAC dependency on any endpoint in this file** — only `get_request_id`/`get_session`/`get_alpaca_broker_client`; any authenticated role can read all portfolio/risk/factor data (plausibly intentional — this is the dashboard's core read surface, but it's the only major domain file with zero role gating on 12 endpoints). (b) `_alpaca_service` and the three call sites (`get_portfolio_summary`, `get_portfolio_holdings`, `get_portfolio_allocation`) wrap the Alpaca live-account path in bare `except Exception: pass`/`except Exception: return None`, silently falling back to the DB-backed service on *any* failure (auth error, network error, malformed response) — swallows errors that might indicate a broker misconfiguration rather than a benign "no live account" state. This mirrors the exception-swallowing pattern the repo's own recent commit history (`d8d5c91d2 fix: revert broad exception swallowing in portfolio_construction_service`) flagged as a problem elsewhere. (c) Simulation-mode detection (`ctrl.trading_mode == "simulation"` forces DB-only reads so the frontend shows backtest results instead of the live broker account) is a deliberate, sensible guard — but it's itself wrapped in a bare `except Exception: pass`.

### src/autonomous_trading_platform/interfaces/rest/routes/settings_routes.py (249 lines)
- Purpose: Operator settings API: `GET`/`PUT /settings` (risk tolerance, drawdown limits, automation toggles, cost-model knobs), `GET /settings/risk-profile`, `GET /settings/advanced` (per-strategy drawdown overrides, asset position caps, cost model).
- Notable: (a) `GET /settings` and `GET /risk-profile`/`/advanced` require only `require_operator_or_admin`, but `PUT /settings` requires the stricter `require_admin` — sensible read/write role split. (b) `_settings_response` hand-builds a large `metadata` dict documenting which settings fields are live vs. `"deprecated_persisted_only"` and what the actual source-of-truth table is (e.g. `min_sharpe_for_promotion` deprecated in favor of `promotion_rules.min_sharpe`) — an explicit, self-documenting migration/deprecation map embedded in the API response, unusually transparent about legacy field drift. (c) `get_advanced_settings` sets `read_only=role != "admin"` by reading `request.state.role` directly rather than via a dependency — duplicates role logic already available through the RBAC dependency system.

### src/autonomous_trading_platform/interfaces/rest/routes/shadow_routes.py (247 lines)
- Purpose: Shadow-mode validation API: start a shadow run comparing simulation vs. live, list/get runs, finalize a run (compute validation summary), list divergences, and check promotion eligibility.
- Notable: All handlers gated by `require_operator_or_admin`. Uses `UUID` path/body types directly (FastAPI validates format before the handler runs) rather than `str` — stricter than most other route files. Two handlers do a local `from fastapi import HTTPException` inside the function body instead of top-level (same minor pattern as portfolio_construction_routes.py).

### src/autonomous_trading_platform/interfaces/rest/routes/system_routes.py (100 lines)
- Purpose: `GET /system/health` (basic), `GET /system/health/detailed` (broker+DB+job diagnostics via `DetailedSystemHealthService`), `PUT /system/trading-mode` (simulation/paper/live transition).
- Notable: Health endpoints have no RBAC gate (any authenticated role); `PUT /trading-mode` requires `require_operator_or_admin` and maps a `ValueError` from the service to `409 Conflict` — appropriate use of 409 for an invalid state transition rather than 422/400.

### src/autonomous_trading_platform/interfaces/rest/schemas/active_strategies_schema.py (309 lines)
- Purpose: Largest schema file — backs strategies_routes.py: active-strategy list, allocation update request/response, enable/disable, governance transition, strategy catalog list/detail/compare, equity-curve points, health + health-lifecycle response models, experiment create/list/detail models.
- Notable: Good use of `Literal` unions for closed enums (`StrategyStatus`, `ExperimentType`, `ExperimentStatus`) instead of loose `str`, giving OpenAPI clients a real enum rather than free text.

### src/autonomous_trading_platform/interfaces/rest/schemas/activity_schema.py (17 lines)
- Purpose: `RecentActivityItemResponse`/`RecentActivityResponse` — event_type/description/timestamp/severity for the dashboard activity feed.

### src/autonomous_trading_platform/interfaces/rest/schemas/alert_schemas.py (63 lines)
- Purpose: Operational-alert request/response models: full alert record, create request, and action requests (acknowledge/resolve/snooze/note).

### src/autonomous_trading_platform/interfaces/rest/schemas/audit_log_schema.py (26 lines)
- Purpose: Audit-log event + pagination + list response models.

### src/autonomous_trading_platform/interfaces/rest/schemas/control_schema.py (51 lines)
- Purpose: Kill-switch, pause/resume action, per-strategy control state, and aggregate controls-state response models.

### src/autonomous_trading_platform/interfaces/rest/schemas/dataset_version_schemas.py (26 lines)
- Purpose: `CreateDatasetVersionRequest` + shared `MetadataActionResponse` message wrapper for metadata_routes.py.

### src/autonomous_trading_platform/interfaces/rest/schemas/feature_dataset_version_schema.py (25 lines)
- Purpose: `CreateFeatureDatasetVersionRequest` for feature-dataset-version registration.

### src/autonomous_trading_platform/interfaces/rest/schemas/ingestion_run_schemas.py (50 lines)
- Purpose: Ingestion-run create/fail request models plus `LatestDatasetVersionResponse`/`LatestFeatureVersionResponse` for the "latest version" lookups.
- Notable: Redeclares `MetadataActionResponse` (also defined in dataset_version_schemas.py) — a duplicate class definition across two schema files rather than a shared import; both are structurally identical (`message: str`), so it's harmless but a minor DRY violation.

### src/autonomous_trading_platform/interfaces/rest/schemas/metrics_schemas.py (92 lines)
- Purpose: Metric-lineage response models: lineage metadata, research/live/blended metrics, blended history, and the lineage-summary response.

### src/autonomous_trading_platform/interfaces/rest/schemas/operations_schemas.py (52 lines)
- Purpose: Job summary/job-run/runtime-state response models for operations_routes.py.

### src/autonomous_trading_platform/interfaces/rest/schemas/portfolio_schemas.py (158 lines)
- Purpose: Second-largest schema file: portfolio summary/equity-curve/performance/holdings/allocation/risk/period-return models plus factor-exposure snapshot/history and factor-neutralization config/run/history models for portfolio_routes.py.

### src/autonomous_trading_platform/interfaces/rest/schemas/settings_schema.py (124 lines)
- Purpose: Operator settings response/update-request models (risk tolerance, drawdown limits, slippage/cost model, automation toggles) plus advanced-settings response (per-strategy overrides, position caps, cost model).
- Notable: Explicitly marks two fields `deprecated` via `json_schema_extra={"deprecated": True}` (`min_sharpe_for_promotion`, `min_paper_trading_period_days`) — deprecation surfaced in the OpenAPI schema itself, consistent with the deprecation-map documented in settings_routes.py.

### src/autonomous_trading_platform/interfaces/rest/schemas/shadow_schemas.py (97 lines)
- Purpose: Shadow-run request/response models: start-run request, run manifest, validation summary, divergence record/list, promotion-eligibility response.

### src/autonomous_trading_platform/interfaces/rest/schemas/system_schemas.py (26 lines)
- Purpose: System health response and trading-mode update request/response models.

---

## (a) Standout candidates (for portfolio writeup)

- `src/autonomous_trading_platform/interfaces/rest/routes/governance_audit_routes.py` — append-only audit log with an explicit supersession-chain amendment model (corrections via new linked events, never mutation of history); good design talking point.
- `src/autonomous_trading_platform/interfaces/rest/routes/metrics_routes.py` — confidence-adaptive research/live metric blending API, with docstrings that explain *why* an allocation decision looks the way it does; strong example of self-documenting operator-facing code.
- `src/autonomous_trading_platform/interfaces/rest/routes/settings_routes.py` — `_settings_response`'s embedded source-of-truth/deprecation map is a good example of proactively documenting config drift instead of leaving stale fields silently misleading.
- `src/autonomous_trading_platform/api/` package as a whole (envelope + error taxonomy + exception handlers + RBAC dependencies) — a clean, consistently-applied cross-cutting HTTP infrastructure layer; every one of the 14 domain route files uses the same `SuccessEnvelope`/`success_response`/`get_request_id` contract except one (see gaps).
- `src/autonomous_trading_platform/config/settings.py` + `broker_config_validator.py` — fail-safe-by-default environment isolation (paper/live) enforced at config load time, not just at runtime gates.

## (b) Gaps/smells

1. **`metadata_routes.py` is a consistency outlier**: no RBAC dependency, no `SuccessEnvelope`, no `request_id` — every other of the 14 route files uses all three. Its mutating endpoints (dataset-version/ingestion-run/feature-dataset-version creation, run completion/failure) are reachable by any authenticated role. Likely intentional (pipeline-internal, machine-called), but worth confirming it isn't reachable from a human-facing surface.
2. **Likely live bug in `governance_audit_routes.py::supersede_governance_audit_event`**: `actor = getattr(auth, "sub", "operator")` where `auth` is the return value of `require_operator_or_admin` (a plain `str` user_id with no `.sub` attribute) — so `actor` always falls through to the hardcoded `"operator"` string, meaning actor attribution on every `/supersede` call is wrong regardless of who actually called it.
3. **`portfolio_routes.py` has zero RBAC gating** across all 12 endpoints (only request_id/session/broker-client deps) — the only major domain file with no role check at all. Combined with broad `except Exception: pass`/`except Exception: return None` around the Alpaca live-account fallback path (3 call sites), errors that should surface (broker misconfig, auth failure) are silently masked as "fall back to DB".
4. **Two route files (`drawdown_governance_routes.py`, `governance_audit_routes.py`) define their response/request Pydantic models inline in the route module** instead of in `interfaces/rest/schemas/`, breaking the file-per-domain schema convention every other route follows.
5. **Duplicate `MetadataActionResponse` class** defined identically in both `schemas/dataset_version_schemas.py` and `schemas/ingestion_run_schemas.py` instead of one shared definition.
6. **Route-shadowing bug risk in `strategies_routes.py`** (carried over from prior entry): `GET /strategies/{strategy_id}` registered before `GET /strategies/health`.
7. Minor repeated smells: local `from fastapi import HTTPException` inside function bodies instead of top-level imports (portfolio_construction_routes.py x2, shadow_routes.py x2); manual `.strip()`-then-422 rationale/reason validation duplicating what Pydantic `Field(min_length=1)` already partially covers (control_routes.py); repeated `except Exception as exc: raise _alert_error(exc)` boilerplate across 6 handlers in operations_routes.py that could be a shared dependency/exception handler instead.
8. Carried over from the pre-existing header: `trading_cycle_timeout_seconds` double-assigned in `config/settings.py` (dead first assignment); `api/auth_middleware.py` reads `JWT_SECRET` via raw `os.environ` at import time rather than through `Settings`; `api/deprecation.py` machinery is unused (no route currently applies `@deprecated`).

## (c) Coverage

- `interfaces/`: 33 of 33 .py files read (14 route files, 14 schema files, 4 package-marker inits, `app.py`). None skipped.
- `api/`: 9 of 9 .py files read. None skipped.
- `config/`: 4 of 4 .py files read. None skipped.
- `common/`: 4 of 4 .py files read. None skipped.
- Total: 50 of 50 files in scope read in full. No sampling — every file was read end-to-end, not excerpted.
