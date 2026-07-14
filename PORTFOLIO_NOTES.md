# Portfolio & Resume Notes — Autonomous Trading Platform

Working notes from a full-codebase audit (2026-07-03), meant as raw material for a portfolio writeup and resume bullets. Not project documentation — trim/edit freely, this file is for you.

---

## 1. One-paragraph pitch

A full-stack autonomous quantitative trading platform: a FastAPI backend with a strictly layered architecture (contracts → storage → application → interfaces), a defense-in-depth safety system gating live trading through four independent checks, a research platform with walk-forward validation and a from-scratch Black-Litterman implementation, and a React/TypeScript frontend now wired to real endpoints via TanStack Query. ~685 commits over ~4 months, 89 Alembic migrations, 362 test files, real Postgres-backed CI.

---

## 2. Scale, at a glance

| Metric | Value |
|---|---|
| Commits | 685 (first commit 2026-02-27, most recent 2026-07-03 — ~4 months) |
| Backend LOC (contracts+storage+application+interfaces+observability) | ~54,500 lines across ~380 files |
| `application/services/` | 70 files, ~26,700 lines — largest single layer |
| `storage/` | 170 files, ~13,100 lines (SQLAlchemy SoR + versioned Parquet) |
| REST API | 32 files, ~5,000 lines, ~90 route handlers under `/api/v1` |
| Alembic migrations | 89 |
| Test files | 362 Python files, ~4,080 `test_` functions |
| Docs | 150 markdown files under `docs/` |
| Frontend pages | 6 (`Dashboard`, `Portfolio`, `StrategyLab`, `Controls`, `Settings`, `ExperimentLab`), ~5,200 lines |

---

## 3. Architecture highlights (the "how it's built" story)

- **Strict layered architecture**: contracts (Pydantic, no logic) → storage (SQLAlchemy SoR + versioned Parquet) → application (services, no I/O) → interfaces (FastAPI/CLI). Enforced directionally — inward dependencies only.
- **UnitOfWork pattern**: `SorUnitOfWork` wires ~29 repositories onto one SQLAlchemy session with proper commit/rollback semantics and transaction reuse — textbook implementation, not a toy version.
- **Immutable, versioned Parquet datasets**: writer enforces append-once semantics (raises `FileExistsError` if a version already has data), auto-generates Hive-style partitioning, checksums every write, and emits a sidecar metadata manifest — a homegrown data-lineage system on top of PyArrow.
- **Contract-level validation as a rule engine**: a generic `Rule[T]`/`ValidationContext`/`Violation` framework (`validators/core.py`) with composable invariant checks per contract, separate from the Pydantic models themselves.
- **Middleware chain with RBAC baked in**: `RequestID → Logging → JWT → Deprecation`, with JWT middleware enforcing a closed role set (`operator`, `researcher`, `risk_manager`, `admin`) at the auth layer rather than in route handlers.
- **OpenTelemetry-first observability**: custom span helper auto-injects run-scoped context (correlation_id, run_id, strategy_id, dataset_version...) onto every trace; a 1,932-line metrics module pairs OTel counters/histograms with observable-gauge callbacks for cycle/order/reconciliation telemetry.

---

## 4. Standout systems (the "why it's impressive" story)

These are the pieces most likely to differentiate this from a typical portfolio project — each demonstrates real domain knowledge, not just CRUD-with-a-database.

**Safety architecture (defense in depth)**
Four independent, chained gates before any live order: environment/build gate → account allowlist → armed/disarmed runtime gate → DB-persisted kill switch (survives restarts). Layered on top: pre-trade risk checks (gross/per-symbol/sector concentration, daily notional caps, configurable unknown-sector policy), SHA-256-keyed order idempotency, and a global shadow-mode ("compute but suppress") toggle. Every breach emits structured audit events + metrics before raising.

**Anti-bias research guards as first-class services, not just test discipline**
- `SurvivorshipGuard` rejects backtests whose universe isn't point-in-time anchored, and diffs requested symbols against a known-future-symbols set built from IPO/corporate-action records to catch look-ahead-via-universe.
- `lookahead_guard_service.py` enforces strictly-increasing simulation timelines and rejects any bar at or after the current simulation timestamp.
- These are runtime-enforced invariants that raise exceptions, not code review conventions.

**From-scratch Black-Litterman implementation**
Computes market-implied prior returns, builds view/confidence matrices, derives posterior returns and covariance via matrix inversion, and feeds a mean-variance optimizer — with an explicit `_enforce_research_only` guard that raises if invoked from any live/paper context, and SHA-256 hashing of all inputs/outputs for reproducibility.

**Universe governance**
Versioned, atomic universe rotation: propose → pre-activation validation (churn caps, symbol format, size bounds) → retire old version → activate new → audit record with config-hash idempotency. Rollback reactivates a prior version by copying members forward rather than mutating history — full auditability preserved.

**Execution realism**
TWAP/VWAP-lite order slicing, slippage modeling, a broker-reconciliation service with tiered drift severity (WARN/CRITICAL thresholds on cash, equity, position qty) and duplicate-fill detection heuristics, plus dynamic position scaling by drawdown/Sharpe/volatility.

**Strategy governance lifecycle**
A full promote/demote/reallocate state machine (`auto_promotion_service`, `auto_demotion_service`, `drawdown_governance_service`, `quality_based_reallocation_service`) driving strategies between research → paper → live based on live quality scores and drawdown ladders — plus a declarative composite-strategy rule DSL (reusable threshold/comparison/crossover primitives + a component registry) alongside hardcoded strategy classes.

**Deterministic replay / chaos testing harness**
`platform_replay/` (14 files) lets a platform run be replayed with injected faults across execution, governance, risk, and admin subsystems — a real fault-injection framework, not just unit mocks.

**Research platform**
Multi-stage pipeline (simulation → walk-forward → Monte Carlo → regime stages), an evolutionary strategy-generation engine, ML-flavored meta-research (overfitting estimation, strategy clustering, robustness prediction), and walk-forward validation computing fold-consistency and Sharpe-degradation stability scores.

---

## 5. Frontend

- React 19 + Vite + TypeScript (strict — `noUnusedLocals`, `verbatimModuleSyntax`, `erasableSyntaxOnly` all on), TanStack Router + Query, Zustand, Recharts, TanStack Table.
- **Important correction to CLAUDE.md**: the frontend is no longer a pure mockup. A real `src/api/http.ts` (axios + JWT bearer) and a full `src/services/` layer now call actual backend endpoints (`/api/v1/portfolio/summary`, `/equity-curve`, etc.) via React Query hooks. This happened after the CLAUDE.md snapshot was written (commits `c776bfa8c`, `cc89b88ae`) — worth updating CLAUDE.md, and worth mentioning in the portfolio as "wired end-to-end," not "mock UI."
- Solid data-viz: Recharts `AreaChart`/`ComposedChart` for equity/drawdown, TanStack Table sortable grids for holdings/strategies.
- Mock data (still used for design/dev) is unusually realistic — per-strategy Sharpe/CAGR/drawdown/win-rate with governance state, operator-attributed audit log entries, realistic experiment-sweep results (512 strategies swept, 47 passed filters).
- **Honest gaps** (say these yourself before an interviewer finds them): shadcn `ui/index.ts` is an empty stub — dependency present, never actually used; `framer-motion` installed but zero imports anywhere; Vitest/Playwright/Testing Library installed but **no test files exist** in the repo. This is "tooling scaffolded ahead of use," not broken — but don't claim frontend test coverage.

---

## 6. Engineering practices worth naming explicitly

- 89 Alembic migrations show iterative hardening of the order-state-machine over time (adding `pending_new`/`pending_cancel`/`expired` statuses, settlement-aware cash snapshots, kill-switch state, drawdown ladders) — evidence of production-grade schema evolution, not a big-bang design.
- CI (`.github/workflows/ci.yml`) runs against a **real dockerized Postgres**, not just SQLite, plus a manual-dispatch smoke job against real Alpaca paper endpoints, explicitly guarded by `NO_LIVE_TRADING`/`ENABLE_LIVE_TRADING` flags.
- `tests/conftest.py` patches SQLite to emulate Postgres-specific types (JSONB, ARRAY, UUID) for fast unit tests, while `tests/utilities/` provides 8 full seeded end-to-end scenario builders (market ingestion, paper-trading golden path, historical-research golden path, etc.) — integration-style coverage of entire pipelines, not just isolated units.
- ruff + mypy + pre-commit, pinned and enforced.
- Clear project history: early phases were formally spec-locked (docs/archived-docs/ preserves "Phase 0–6" canonical specs: contracts canon, SoR design, universe governance, safety architecture, scheduler state machines, ingestion SLA policy) before shifting to feature-driven iteration — a good "how do you approach ambiguous greenfield work" story for interviews.

---

## 7. Draft resume bullets (edit to taste)

- Designed and built a layered quantitative trading platform (FastAPI/PostgreSQL/React) enforcing strict contract→storage→application→interface boundaries across ~55K lines of backend code and 90+ REST endpoints.
- Implemented a defense-in-depth safety system for live trading — four independent gating layers, pre-trade risk limits (concentration, notional, sector exposure), and idempotent order submission — preventing unsafe order flow by construction rather than convention.
- Built runtime-enforced anti-bias guards (survivorship, look-ahead) as first-class services that reject invalid backtests, rather than relying on code review or test discipline.
- Implemented a from-scratch Black-Litterman portfolio optimizer with research/production context isolation and full input/output reproducibility hashing.
- Designed a versioned, checksummed, immutable Parquet dataset layer with Hive-style partitioning as a lightweight data-lineage system for market data.
- Built a full strategy governance lifecycle (auto-promotion/demotion, drawdown ladders, quality-based reallocation) driving strategies between research, paper, and live states based on real-time performance metrics.
- Maintained 89 iteratively-evolved Alembic migrations and a Postgres-backed CI pipeline with 4,000+ tests across unit/integration/smoke tiers.

---

## 8. Portfolio page structure suggestion

1. **Hero**: one-line pitch + architecture diagram (layered boxes: contracts/storage/application/interfaces, or the safety-gate chain).
2. **The safety story**: this is your most differentiated content — walk through the 4-gate chain and pre-trade risk checks with a code snippet or sequence diagram. Most portfolio trading projects skip this entirely.
3. **The research rigor story**: survivorship/look-ahead guards, walk-forward validation, Black-Litterman. Shows you understand *why* naive backtests lie.
4. **Screenshots of the wired frontend** (Dashboard equity curve, Controls kill switch, Strategy Lab comparison table) — now legitimately "full-stack, live-wired," not just a mockup.
5. **Scale/rigor callout box**: commit count, migration count, test count, CI setup — signals sustained engineering discipline over a flashy demo.
6. **Honest "what's next" section**: naming the frontend test gap and any remaining stale docs shows maturity rather than hiding it.

## 9. Before you publish

- Update CLAUDE.md's frontend description — it still says "static mockup, no real API calls," which is now false and will read as a stale doc to anyone diffing your claims against the repo.
- `docs/architecture/safety-doctrine.md`, `invariants.md`, and `v1-boundaries.md` are referenced in CLAUDE.md but don't exist (only `data-flow.md`, `layering.md`, `system-overview.md` do) — either write them or fix the references before pointing recruiters/interviewers at the docs folder.
- Consider a short demo video or GIF of the Controls page kill switch + Dashboard equity curve — the safety system is hard to convey in static screenshots but is your strongest differentiator.
