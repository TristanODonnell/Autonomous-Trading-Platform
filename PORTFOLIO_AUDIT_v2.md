# Portfolio Audit v2 — Autonomous Trading Platform

Full-codebase audit completed 2026-07-09. Every claim below is backed by a file path, and every number by a command run during the audit (raw command output in `audit/01_metrics.txt`; per-file findings in `audit/raw/*.md`, one file per audited scope). The prior draft (`PORTFOLIO_NOTES.md`) was treated as hypothesis, not ground truth — see §7 for what changed.

---

## 1. One-paragraph pitch

A full-stack autonomous quantitative trading platform: a FastAPI/PostgreSQL backend of ~139K lines of Python across 23 subpackages, with a defense-in-depth safety system whose four live-trading gates chain in a single readable method (`safety/services/live_trading_gate_service.py:53-68`) and whose kill switch is a DB-persisted singleton that survives restarts (`infra/db/alembic/versions/uu54vv66ww07_add_kill_switch_state_table.py`); runtime-enforced anti-bias research guards (`research/simulation/services/lookahead_guard_service.py`, `universe/services/survivorship_guard.py`); a from-scratch, numerically-tested Black-Litterman implementation (`research/black_litterman/black_litterman_research_service.py`); and a React 19 frontend wired end-to-end to 24 real REST endpoints via axios + JWT + TanStack Query (`frontend/src/api/http.ts`, `frontend/src/services/`). 683 commits over ~4 months, 89 Alembic migrations, 4,080 test functions across 362 files, CI against a real dockerized Postgres.

---

## 2. Scale, at a glance

Every number re-verified this audit; commands and raw output preserved in `audit/01_metrics.txt`.

| Metric | Value | Command |
|---|---|---|
| Commits | **683** (2026-02-27 → 2026-07-02) | `git log --oneline \| wc -l` |
| Backend LOC, 5 core layers (contracts+storage+application+interfaces+observability) | **54,558** across **382** files | `find <dirs> -name '*.py' -exec cat {} + \| wc -l` |
| All of `src/` | **884** files, ~139K LOC, 23 subpackages | per-dir `find`/`wc` loop |
| `application/services/` | 70 files, 26,667 LOC | same |
| `storage/` | 170 files, 13,073 LOC | same |
| `research/` | 130 files, 19,082 LOC | same |
| `cli/` | 25 files, **22,986 LOC**, 326 leaf commands (175 + 151 per half) | same + command-registration counts in `audit/raw/cli_1.md`, `cli_2.md` |
| REST API | 32 files, 4,981 LOC, **90 route handlers** | `grep -rE '@router\.(get\|post\|put\|patch\|delete)' \| wc -l` |
| Alembic migrations | **89** (the 90th dir entry is `__pycache__`) | `find infra/db/alembic/versions -name '*.py' -not -name '__init__.py' \| wc -l` |
| Tests | **362 files, 4,080 test functions, 86,651 LOC** | `grep -rE '^\s*(async )?def test_' tests/ \| wc -l` |
| Docs | 150 markdown files, 23,238 LOC | `find docs -name '*.md' \| wc -l` |
| Frontend | 59 tracked files, 6,406 LOC (4,293 in pages) | `git ls-files -- frontend`, `wc -l` |
| Committed market data | 31,475 Parquet files under `data/` | `git ls-files -- data/ \| wc -l` |
| TODO/FIXME/XXX in `src/` | **3** (13 in frontend) | `grep -rn 'TODO\|FIXME\|XXX'` |

Cross-check that the test total is internally consistent: 1,075 (tests/research) + 984 (tests/application+cli) + 1,356 (tests/execution+strategy+scheduler+universe) + 665 (all remaining dirs) = **4,080** — matches the independent repo-wide grep exactly.

---

## 3. Architecture highlights

- **Layered architecture — real, with honest caveats.** contracts (Pydantic) → storage (SQLAlchemy SoR + versioned Parquet) → application → interfaces holds as the dominant structure, but the audit found real violations worth owning: 17 of 37 M–Z services use the ORM directly rather than repositories (`audit/raw/application_services_m-z.md` §Gaps), contracts import from `governance/` and `storage/` in three places (`contracts/governance/strategy_governance.py`, `contracts/runtime/run_manifest.py`, `contracts/validators/dataset_version.py`), and storage imports back from `execution/` (`storage/sor/models/strategy_runtime_states.py`). Present it as "layered with documented exceptions," not "strict."
- **UnitOfWork over 30 repositories** — `storage/sor/unit_of_work.py` wires 30 repositories onto one session with commit/rollback semantics. Caveat: that's 30 of ~64 total SoR repositories; governance/shadow/health/risk-budget domains are instantiated ad hoc outside the UoW (full list in `audit/raw/storage.md`).
- **Immutable, versioned Parquet datasets** — append-once semantics (raises `FileExistsError` on re-write of a version), Hive-style partitioning, checksums, sidecar manifests (`storage/parquet/writer.py`, `storage/parquet/versioning.py`).
- **Contract-level rule engine** — generic `Rule[T]`/`ValidationContext`/`Violation` framework in `contracts/validators/core.py` (frozen `Generic[T]` dataclass rules; a broken rule's own exception is converted into an ERROR violation instead of crashing), composed by 17 validator modules. Note: do **not** claim contracts are frozen/immutable — only one Pydantic model uses `frozen=True` (`contracts/simulation/dividend_event.py`) and 27 of 63 dataclasses aren't frozen.
- **Typed money and time at the foundation** — `contracts/common/types.py` (26 lines) defines `UTCDateTime` (UTC enforced at parse time) and `Money = Decimal`, used across the entire contracts package — eliminating naive-datetime and float-money bug classes by construction.
- **RBAC-in-middleware REST layer** — a consistent envelope/error-taxonomy/exception-handler/RBAC infrastructure applied uniformly across 14 domain route files (`src/autonomous_trading_platform/api/`, per `audit/raw/interfaces_api_config_common.md`), with one deliberate outlier (`metadata_routes.py`) and one real gap (`portfolio_routes.py`, zero RBAC on 12 endpoints — see §10).
- **OpenTelemetry-first observability** — `observability/metrics.py` is exactly **1,932 lines with 261 OTel instruments** (126 counters, 129 histograms, 4 observable gauges); `observability/tracing.py` + `runtime_context.py` auto-stamp 8 `ratp.*` run-scoped attributes (correlation_id, run_id, strategy_id, dataset_version…) on every span opened via the `start_span` helper; `correlation_links.py` generates Grafana Tempo/Loki deep links.
- **Schema-drift tripwire** — `tests/storage/schema_drift/test_contract_model_schema_drift.py` diffs 28 Pydantic-contract/ORM-model pairs field-by-field on every test run, plus an integration-marked half that runs real `alembic upgrade head` against Postgres and diffs ORM vs. actual DB columns. A machine-checked guarantee of the "contracts define all data shapes" rule.

---

## 4. Standout systems

**Safety architecture (the strongest differentiator — every sub-claim now has line-level evidence)**
- Four chained gates in one readable method: environment/build/config → account allowlist → armed/disarmed runtime gate → kill switch (`safety/services/live_trading_gate_service.py:53-68`), each gate raising a *distinct typed exception* for precise incident diagnosis.
- DB-persisted kill switch surviving restarts: `safety/services/kill_switch_service.py` + `storage/sor/repositories/core/kill_switch_state_repository.py`; the migration adding it says so explicitly ("survives service restarts, deploys, and crashes" — `uu54vv66ww07`). Tested for real persistence: a fresh service instance re-reads persisted state (`tests/safety/test_kill_switch_persistence.py`, 14 tests).
- Two-layer NO_LIVE_TRADING enforcement: config/build-time (`safety/environment_policy.py:13-22`) plus runtime re-check at execution time against live DB state (`runtime/replay_debug.py:387-399`).
- Shadow mode ("compute but suppress") verified end-to-end: `safety/services/shadow_mode_service.py` (toggle) → `scheduler/jobs/run_order_submission_job.py:78,217` — orders are computed and all pre-trade checks run, then `if shadow_mode_enabled: continue` skips only broker submission.
- Pre-trade risk: gross/per-symbol/daily-notional caps plus sector concentration with a configurable 3-way unknown-sector policy (reject / warn-allow / unknown-bucket) — `safety/services/pre_trade_risk_service.py`; sector and portfolio-symbol breaches audit-log + emit metrics before raising (`:463-480`, `:282-320`).
- DB-level defense in depth: live-promotion CHECK constraints (`min_days_tested >= 30`, `min_trade_count > 0` for `approved_live` transitions) enforced in Postgres itself (`zz09aa21bb32_add_promotion_rules_live_constraints.py`).
- tests/safety/ is the densest test directory in the repo (153 tests / 3,219 LOC), with a repeated "reader math → enforcement → audit/observability → regression-guard" house style (`tests/safety/test_sector_concentration.py`, 33 tests in 9 classes).

**Anti-bias research guards, runtime-enforced and numerically tested**
- `research/simulation/services/lookahead_guard_service.py` rejects any bar at/after the simulation timestamp; wired into every context build via `strategy/contexts/strategy_context_builder.py` (explicit `assert_historical_only()` on every path).
- `universe/services/survivorship_guard.py` + `research/validation` survivorship checks; universe membership is point-in-time anchored via `first_seen`/`last_seen` tracking (`universe/services/raw_market_pool_refresh_service.py`).
- These are tested with exact-count assertions, not smoke tests: `tests/research/simulation/test_lookahead_guard.py`, `tests/research/validation/test_survivorship_validation.py`, and `tests/research/regimes/test_regime_join_service.py::test_no_lookahead_bias_exact_join_only` (exact-timestamp join only, no forward-fill).

**From-scratch Black-Litterman — every sub-claim verified**
`research/black_litterman/black_litterman_research_service.py` (668 lines): equilibrium implied prior, P/Q/Omega view matrices, closed-form posterior via matrix inversion, closed-form MVO weights with Lagrange multiplier, `_enforce_research_only` guard, SHA-256 hashing of inputs/views/outputs. Tested numerically against the CAPM formula: `tests/research/test_black_litterman_research_service.py::test_implied_prior_return_calculation` uses `np.testing.assert_allclose(prior, risk_aversion * cov @ benchmark)` and asserts constrained weights sum to exactly 1.0.

**Simulation realism**
- `research/simulation/services/simulation_execution_engine.py` (1,069 lines): settled/unsettled/reserved cash tracking, bar-indexed T+N settlement, ex-date cash dividends, latency-aware order scheduling, buying-power-gated acceptance.
- `simulated_execution_service.py`: seeded-RNG stochastic rejection, gap-open vs. intrabar limit-touch eligibility, volume-participation capping, probabilistic partial fills — all deterministically reproducible per run; byte-identical replay is a CI regression gate (`tests/research/simulation/test_deterministic_replay.py`).
- Sim/live behavioral parity: the simulation planner reuses the *same* TWAP/VWAP/order-type-resolver code as live trading (`research/simulation/services/simulation_execution_model.py`), and `tests/strategy/test_indicator_feature_equivalence.py` cross-validates live-strategy indicator math against the offline feature-engineering services.
- Closed-loop calibration: realized paper-trading fill quality recalibrates the simulator's slippage model, with min-30-fill sample gating and coefficient clamping (`research/calibration/services/slippage_calibration_service.py`).

**The strategy-research experiment stack (generation → staged funnel → validation)**

This is its own architecture piece, distinct from strategy governance: governance decides what is *allowed to trade* (promotion/demotion, allocation, drawdown ladders); the experiment stack decides what is *worth testing* and whether its backtest results can be trusted. It reads as one funnel: generate candidates → simulate cheaply → filter → simulate expensively → walk-forward → Monte Carlo → validation scoring → (optionally) auto-seed survivors into governance as `approved_research`.

- **Strategy generation is parameter-space sampling over a typed registry, not ML.** Three interchangeable generators sit behind one engine (`research/strategy_generation/strategy_generation_engine.py`): *grid* — full Cartesian product over per-parameter value lists via `itertools.product` (`generators/grid_search_generator.py`); *random* — seeded `random.choice` draws per parameter, default `n_samples=50` (`generators/random_sampling_generator.py`); *"evolutionary"* — a mutation-only sampler (registry defaults + random pool, then each generation deep-copies every member and flips each parameter with `mutation_rate=0.25`), yielding `population_size × (generations+1)` candidates (80 at defaults). **It has no fitness function, selection, or crossover — call it "mutation-based candidate generation," never "genetic/evolutionary search"** (`generators/evolutionary_generator.py`; its own docstring says "minimal deterministic mutation-driven candidate generator"). Legal parameter values come from one shared source of truth: the strategy registry's declared `ParameterSpec` bounds/steps (`parameter_space_resolver.py`), so all three generators sweep the same validated space. The composite-rule path is different and more bespoke: it combinatorially assembles strategy *skeletons* (indicator + rule + aggregator + filter) from a hand-curated domain table that classifies ~15 indicators by value domain (zero-centered / ratio / 0–100 / boolean) to decide which rule types are semantically valid — a documented pool of ~854 skeletons, each validated against the live `ComponentRegistry` before acceptance (`strategy_generation/composite_generation.py`). Dedup is by SHA-256 `config_hash`, in-session and cross-session (`cache/strategy_generation_cache.py`), and every rejected/duplicate candidate is recorded with its reason (`generation_result.py`).
- **The staged funnel: survivors of each stage feed the next** (`research/pipeline/pipeline_runner.py` — "final_survivors … the elite set"). Four stage types are registered (`pipeline/stages/stage_registry.py`): **Stage 1 "cheap"** — one short-window simulation per candidate with loose filters to kill catastrophically bad configs before spending compute; **Stage 2 "intermediate"** — full-window simulation with real thresholds; **Stage 3 walk-forward**; **Stage 4 Monte Carlo**. The regime stage remains a 0-byte stub (`pipeline/stages/regime_stage.py`) — regime *validation* exists, the regime pipeline *stage* does not.
- **The pass/no-pass criteria are explicit and enumerable** (`research/experiments/filtering/config.py`, `filters.py`): 8 core checks — min Sharpe (default 1.0), max drawdown (−0.20), min closed round-trip trades (30, via real FIFO lot matching in `metrics/trade_metrics.py`), min consistency score (fraction of profitable equity-curve windows, 0.5), min profit factor, min win rate, max windowed-return variance, min total return — plus 2 robustness checks (per-window Sharpe floor, min profitable-window count) that run only after all core checks pass. Ranking uses a linear composite: `0.4·sharpe + 0.3·return − 0.2·|drawdown| + 0.1·consistency` with per-term contributions retained for explainability (`filtering/scoring.py`).
- **Walk-forward = time robustness** (`pipeline/stages/walk_forward_stage.py`, 555 lines): rolling train/test folds from `train_days/test_days/step_days`; a strategy passes a fold iff it passes filters on **both** the train and test windows (test thresholds deliberately stricter), and survives the stage iff it passes ≥ `min_folds_passed`. The committed config is concrete: 9 folds over 2022–2025, train 365d / test 90d / step 90d, must pass 6 of 9 (`experiments/staged/ma_sweep_staged.yaml`). Downstream, `validation/walk_forward_validation.py` computes fold consistency, train→test Sharpe degradation, and fold-Sharpe stability (1/(1+CoV)) from the fold results.
- **Monte Carlo = structural robustness of a single strategy, via seed resampling — not trade-resampling bootstrap** (`pipeline/stages/monte_carlo_stage.py`, 448 lines): each survivor is re-simulated N times over the *same* window with different seeds, so what varies is the stochastic execution realism (order rejection, limit-touch, partial fills, slippage draws from `simulated_execution_service.py`); it survives iff it clears the filters in ≥ `min_pass_rate` of runs (50 runs, 70% in the committed config). The aggregator deliberately filters per-run rather than on aggregate means — "a mean Sharpe of 1.2 could hide the fact that 40% of runs went badly negative" (`pipeline/aggregation/monte_carlo_aggregator.py`). Caveats to own: MC seeds are `base_seed + run_index` rather than the hashed `DeterministicSeedService` other stages use (collision risk), and all three stages share a fail-open smell — if *every* simulation in a stage returns None, survivors pass through untested.
- **Scale of a cycle**: the committed staged experiment sweeps a 3×3 grid (9 MA-crossover variants) through all four stages (`experiments/staged/ma_sweep_staged.yaml`); generation defaults produce 50 (random) / 80 (mutation) candidates per strategy type, and the composite path can enumerate its full ~854-skeleton pool — the funnel design is what makes larger pools affordable (cheap stage first, MC only for the elite).
- **Determinism/restart plumbing underneath all of it**: SHA-256-derived per-unit seeds keyed on experiment/strategy/config/stage/window/fold (`research/execution/deterministic_seed_service.py`); a ~22-field simulation cache key capturing full lineage so identical sims are skipped (`cache/cache_identity.py`); checkpoint/resume with an identity-mismatch guard that raises "Unsafe checkpoint identity mismatch" rather than silently corrupting a resumed experiment (`checkpoints/research_checkpoint_service.py`, `research_restart_plan.py`); thread-pool parallel execution with deterministic result ordering (`research/execution/parallel_execution_service.py`).
- **Funnel exit**: staged-pipeline survivors are auto-inserted into the governance table as `approved_research` with `submitted_by="system"` (`scheduler/cycles/run_experiment_pipeline_cycle.py`) — the documented hand-off point where the experiment stack ends and the governance lifecycle (§4 above) takes over. Note there is no human gate at this specific seam; that's fine for a research-only state but worth naming.

**Scheduling & orchestration suite**

`scheduler/` is 52 files / 9,905 LOC organized as three tiers (`audit/raw/scheduler_backtesting.md`):
- **Airflow DAGs (4)**: market ingestion every 5 minutes, trading cycle every 5 minutes on weekdays, daily backfill, daily corporate-action ingestion (`scheduler/airflow/dags/`). Cron is deliberately crude; real market-session awareness (holidays, pre/post-market delay-vs-skip decisions) lives in `scheduler/session_safety.py`.
- **A 13-job registry + manual-trigger path**: `scheduler/registry/scheduler_registry.py` declares 13 named jobs (ingestion, features, trading, rebalance, promotion/demotion, correlation/risk/factor monitoring, drawdown ladder, health lifecycle, experiment pipeline) with lock keys and manual-trigger gating enforced by an in-memory no-overlap lock (`registry/manual_trigger_service.py`, `no_overlap_lock.py`). ~10 governance cycles share a disciplined (if duplicated) template: RunManifest + OTel span + metrics + commit/rollback + manifest completion per run (`cycles/governance_automation_common.py`). Honest caveat, already in §10: only 4 of the 13 registered jobs have DAGs — the rest run via manual trigger/CLI.
- **Golden-path orchestrators**: `PaperTradingGoldenPathOrchestrator` (intraday tick = ingestion → features → trading cycle; plus EOD maintenance producing adjusted-bars + feature datasets) and `HistoricalResearchGoldenPathOrchestrator` (backfill → corporate actions → features → experiment pipeline as one tracked run) (`scheduler/orchestration/`); `HistoricalIngestionReplayOrchestrator` replays the *real* production cycle functions on a historical clock, trading disabled by default. The actively-used high-fidelity backtest path (`scheduler/backtest/backtest_trading_cycle_orchestrator.py`) mirrors the production pipeline bar-by-bar and freezes the allocation config for the whole run so live policy edits can't leak into an in-flight backtest (its `FINDING-11` comment).

**Universe governance**
Atomic propose → churn-cap/format/size validation → retire → activate → audit rotation with config-hash idempotency; rollback creates a *new* version copying members forward rather than mutating history (`universe/services/universe_rotation_service.py`). Immutability enforced at the repository level with a real state machine and custom exceptions (`storage/sor/repositories/core/universe_version_repository.py`), tested against a real SQLite session (`tests/universe/test_universe_version_repository_immutability.py`).

**Execution & reconciliation**
- Real TWAP/VWAP-lite slicing and slippage/cost modeling (`execution/policy/twap_slicer.py`, `vwap_lite_slicer.py`, `slippage_calculator.py`) — with the honest caveat that live-mode slicing is a schedule in metadata, not actual child-order routing.
- SHA-256-keyed order idempotency over `run_id|strategy_id|bar_timestamp|symbol|side|qty` (`safety/services/order_idempotency_service.py`).
- Two explicit audit-logged state machines with hard-fail on invalid transition (`execution/services/order_state_machine_service.py` — its test walks the full transition matrix plus an 18-case invalid matrix).
- A single shared monotonic-delta duplicate-fill guard used identically by both polling and streaming fill paths (`execution/services/broker_order_mapper.py::extract_incremental_fill`).
- Portfolio-wide drawdown circuit breaker with explicit fail-closed/fail-open design-principles docstring (`execution/services/portfolio_drawdown_governance_service.py`), distinct from the smoother per-strategy taper (`drawdown_scaling_service.py`), and above both, a **5-rung drawdown governance ladder** (NORMAL→WARNING→PROBATION→SUSPENDED→BREACHED driving allocation_scalar 1.00→0.00, anti-flapping cooldowns, operator acknowledgement required to recover — migration `gg77hh88ii99`).

**Strategy governance lifecycle**
Auto promotion/demotion with fail-closed `source_run_id` requirements for capital-bearing transitions and loud failure on misconfigured promotion rules (`application/services/strategy_governance_service.py`); concurrency-safe idempotent reallocation via `pg_try_advisory_xact_lock` + row-guard fallback + rebalance-run-id idempotency (`application/services/quality_based_reallocation_service.py`); an append-only governance audit log with a supersession-chain amendment model — corrections are new linked events, never mutation of history (`interfaces/rest/routes/governance_audit_routes.py`, `application/services/governance_audit_service.py`). Governance state machines are the most deeply scenario-tested code in the repo (46 + 52 + 24 tests across the drawdown/health-lifecycle/health-monitor suites, `audit/raw/tests_application_cli.md`).

**Composite-strategy DSL**
`strategy/composite/composite_strategy_config.py` + `composite_rule_strategy.py`: a Pydantic config schema cross-validating every component reference against a live `ComponentRegistry` at construction time, a restricted-AST warmup-formula evaluator (not `eval()`), and a per-bar explainability trace of every indicator value, rule decision, and confidence composition. Caveat for the writeup: FILTER/EXIT_RULE/SIZING component types are registered `metadata_only`/non-executable placeholders (`strategy/components/_registrations.py`) — the DSL executes signal rules today, not filters/exits/sizing.

**Platform replay / fault injection**
`application/services/platform_replay/` — YAML-fixture-driven whole-platform replay with a validated failure-injection vocabulary (9 failure kinds × 6 targets, `platform/replay/platform_replay_config.py`, 772 lines) and hooks that inject synthetic incidents (missing/late bars, execution/governance/risk/admin faults) into SoR tables tagged with a replay_run_id (`failure_injection.py`, 456 lines). The fixture suite is documented to an unusual standard (`fixtures/platform/replays/README.md`, 259 lines, including an honest "Known Gaps" section).

**Runtime engineering**
- `scheduler/cycles/run_trading_cycle.py` (1,140 lines) — the most defensively-coded file in the repo: dual kill-switch/freeze/governance checkpoints (cycle-start and mid-cycle), a 3-tier transient/persistent/unknown exception taxonomy with distinct recovery actions, two named degraded-mode escape hatches. Driven end-to-end in tests against kill-switch/paused/disabled/mode-mismatch states with zero-artifact assertions (`tests/scheduler/test_paper_trading_cycle.py`).
- Settings actually drive behavior — proven, not assumed: `tests/runtime/test_risk_parameter_wiring.py` shows risk tolerance, vol targets, and drawdown limits each change real trading-cycle output, including a precedence test that per-strategy drawdown blocks before portfolio-level.

---

## 5. Frontend audit

**The headline: CLAUDE.md is wrong and the prior draft was right — this is a live-wired frontend, not a mockup.** All six pages fetch from **24 distinct real endpoints** through `src/services/*.ts` → `src/api/http.ts` (axios instance with JWT bearer) → TanStack Query. `src/mock/data.ts` (508 lines) has zero imports anywhere — fully dead (`audit/raw/frontend.md`).

Verified stack facts: React 19.2.5; all four tsconfig strictness flags on (`noUnusedLocals`, `noUnusedParameters`, `verbatimModuleSyntax`, `erasableSyntaxOnly`); Zustand store + React Query cache; Recharts Area/Composed charts with custom tooltips and dual Y-axes (Dashboard/Portfolio); TanStack Table (core row model only — no sorting/filtering wired). Best pages: `Controls.tsx` (948 lines — real mutations for kill switch, strategy toggles, trading mode, allocation overrides, governance promotion, each requiring a reason field, with correct cache invalidation) and `ExperimentLab.tsx` (full create-form flow, zero TODOs).

**Must-fix before showing anyone (all in `audit/raw/frontend.md`):**
1. **The build is likely broken**: 9 files import `cn` from `src/lib/utils.ts`, which does not exist. `npm run build` should fail.
2. `StrategyLab.tsx` (598 lines, fully API-wired) is orphaned — `/strategy` routes to `ExperimentLab.tsx`; nothing mounts StrategyLab. 6 of the 13 TODOs live in this unreachable file.
3. 12 of 23 runtime dependencies have zero imports (framer-motion, zod, react-hook-form, lightweight-charts, radix-ui, lucide-react, etc.); vitest/playwright/testing-library installed with zero test files and no configs; `.husky/pre-commit` runs `npm test` but no `test` script exists.
4. Branding drift: the UI says "◈ WeTrade" (`TopNav.tsx`); `index.html` title is still the Vite default "frontend".
5. Dead scaffolding: 6 zero-byte files in `src/routes/` (abandoned file-based-routing convention), 4 empty `export {}` barrel files, unused Vite template assets.

---

## 6. Engineering practices

- **CI against real infrastructure** (`.github/workflows/ci.yml`, verified line-by-line in `audit/raw/infra_ci_configs.md`): the `test` job boots the actual `docker-compose` `postgres:16` container, waits on `pg_isready`, and runs `pytest -m "not external"` against it (so integration-marked tests run in CI against real Postgres). A second `external-alpaca-smoke` job is `workflow_dispatch`-only and triple-guarded: `NO_LIVE_TRADING=true` + `ENABLE_LIVE_TRADING=false` + manual trigger only.
- **Schema evolution as evidence of iteration**: 89 migrations from a single genesis (`e62e75b1c021`, the 11 canonical v1 tables) through a 5-way merge reconciling five parallel risk/governance feature branches (`hh88ii99jj00`). Notable, verified by reading migration bodies: order-state hardening (PENDING_NEW/PENDING_CANCEL/EXPIRED), settlement-aware cash snapshots (F-06), the kill-switch table, the drawdown governance ladder, and live-promotion CHECK constraints. Also honest history: three tables were drop-and-recreated as self-documented drift fixes, and the final migration explicitly creates tables "missing from migration history" — schema drift happened and was caught.
- **Test infrastructure**: `tests/conftest.py` patches SQLite to emulate Postgres JSONB/ARRAY/UUID and auto-sets `APP_ENV=test`/`DATABASE_URL=sqlite:///:memory:`/`NO_LIVE_TRADING=true` (verified with line citations in `audit/raw/tests_remainder.md`); `tests/utilities/` provides 8 layered end-to-end scenario builders that monkeypatch only I/O seams and run real ORM/Parquet/service code; API tests use a real `TestClient` with real HS256 JWTs through the full middleware stack. Test hygiene is exceptional: effectively zero skip/xfail debt across 4,080 tests (7 conditional runtime skips repo-wide, 0 xfail markers).
- **Tooling**: pre-commit pins ruff 0.15.4 + mypy 1.19.1, exactly matching the pip-compile lockfiles; pyproject uses a two-tier loose-constraints + compiled-lockfile strategy.
- **Docs-as-audits culture**: `docs/audits/agent-findings/` contains ground-truth census documents (e.g., the 85-command CLI drift audit) that found and recorded real bugs; `docs/backend/simulation/research_execution_paths.md` maps which of 7 backtest/replay code paths are real vs. legacy vs. debug-only — rare self-awareness.

---

## 7. Corrections from prior draft

Claims in `PORTFOLIO_NOTES.md` verified, corrected, or refuted:

| Prior claim | Verdict |
|---|---|
| 685 commits, through 2026-07-03 | ✗ **683 commits, last 2026-07-02** (history appears to have been rewritten since the draft — re-run before publishing) |
| ~54.5K LOC / ~380 files backend; 70 services / ~26.7K; 32 REST files / ~90 handlers; 89 migrations; 362 test files / ~4,080 tests; 150 docs | ✓ all confirmed exactly |
| Frontend "wired end-to-end, not mock UI" | ✓ confirmed (24 endpoints, mock layer dead) — but the build is currently broken (`src/lib/utils.ts` missing) and one page is orphaned |
| Frontend pages ~5,200 lines | ✗ 4,293 lines in `src/pages/`; 6,406 total tracked frontend LOC |
| "UnitOfWork wires ~29 repositories" | ~✓ 30 — but only 30 of ~64 repositories; several domains sit outside the UoW entirely |
| "Repositories only; never call ORM from services" | ✗ **refuted as a blanket claim** — direct ORM use is widespread in `application/services/` (17 of 37 M–Z files); reframe as "UoW/repository pattern for the core trading path" |
| Contracts are immutable Pydantic shapes with no logic | ✗ overstated — 1 frozen Pydantic model, 27/63 dataclasses unfrozen, several contracts carry real behavior; 3 outward layering imports |
| Strategies include MACD | ✗ **no MACD exists** — `"macd_crossover"` is an alias for the SMA-based `MovingAverageCrossoverStrategy` (`platform_replay/initial_state_hooks.py`). Also fix CLAUDE.md, which names MACD |
| "Evolutionary strategy-generation engine" | ✗ **not evolutionary** — no fitness/selection/crossover; a deterministic mutation-driven candidate generator by its own docstring (`research/strategy_generation/generators/evolutionary_generator.py`). Say "mutation-based candidate generation + downstream quality filtering" |
| "ML-flavored meta-research (overfitting estimation, clustering, robustness prediction)" | ~✗ mostly heuristic scorers that *explicitly disclaim ML in their docstrings*; the genuinely algorithmic pieces are from-scratch single-linkage agglomerative clustering (`intelligence/strategy_clustering_service.py`) and cosine regime similarity. Cite those two; don't say "ML" |
| Multi-stage pipeline "simulation → walk-forward → Monte Carlo → regime" | ~✓ except the regime *stage* is a 0-byte stub (`research/pipeline/stages/regime_stage.py`); regime validation exists, regime pipeline production does not |
| `docs/archived-docs/` preserves "Phase 0–6 spec-locked canonical specs" | ✗ no Phase 0–6 structure exists — it's an 8-subsystem index + a D-001..D-005 decision log; only Phase 8/9 self-labels appear. The spec-first story is still true (CHANGELOG's spec-lock series v0.1.0→v1.0.0 documents 10 spec sign-offs) — just don't cite "Phase 0–6 docs" |
| CLAUDE.md references 3 nonexistent `docs/architecture/` files | ~✓ nuance: `v1-boundaries.md`, `safety-doctrine.md`, `invariants.md` all **exist** — under `docs/archived-docs/`, not `docs/architecture/`. Fix the paths, don't write new docs |
| `platform_replay/` = 14-file deterministic replay/chaos harness | ✓ confirmed, located at `application/services/platform_replay/` — with the caveat that `failure_injection.py` has no internal APP_ENV guard (caller's responsibility) |
| "1,932-line metrics module" | ✓ exact (`wc -l observability/metrics.py` = 1,932; 261 instruments) |
| Middleware chain + closed role set at auth layer | ✓ per `audit/raw/interfaces_api_config_common.md`; one caveat: `api/auth_middleware.py` reads `JWT_SECRET` via raw `os.environ` at import time |
| CI dockerized Postgres + guarded Alpaca smoke job; conftest SQLite patches; 8 scenario builders | ✓ all confirmed with citations |
| *(unmentioned in prior draft)* `cli/` | **New**: 25 files / 22,986 LOC / 326 commands — mostly disciplined thin wrappers, but `cli/commands/backtesting.py` (3,645 lines) is a layering inversion: labeled deprecated in its own help text yet load-bearing for 5 other modules, containing ~2,400 lines of test-harness logic in production `src/` (one verify command toggles the real kill switch) |
| *(unmentioned)* top-level `backtesting/` package | **New**: confirmed dead code — zero production callers; real cost/slippage modeling lives in `research/simulation/` |

---

## 8. Draft resume bullets (revised — every clause survives a repo check)

- Designed and built a layered quantitative trading platform (FastAPI/PostgreSQL/React 19) — ~139K lines of Python across 23 subpackages, 90 REST endpoints, 89 iteratively-evolved Alembic migrations, and a React frontend wired end-to-end to 24 live API endpoints via JWT-authenticated React Query.
- Implemented a defense-in-depth live-trading safety system: four chained gates (environment/build → account allowlist → armed runtime gate → DB-persisted kill switch that survives restarts), two-layer NO_LIVE_TRADING enforcement, shadow-mode compute-but-suppress execution, and pre-trade gross/symbol/notional/sector-concentration limits — backed by DB-level CHECK constraints on live promotion and 153 dedicated safety tests.
- Built runtime-enforced anti-bias guards (look-ahead and survivorship) as services that reject invalid backtests at execution time, verified by exact-assertion tests including exact-timestamp regime joins and next-open fill semantics.
- Implemented Black-Litterman portfolio optimization from scratch (equilibrium priors, view/confidence matrices, closed-form posterior and MVO weights), research-context-isolated and SHA-256-hashed for reproducibility, with the implied-prior formula unit-tested against CAPM via `np.testing.assert_allclose`.
- Designed a versioned, checksummed, append-once Parquet dataset layer with Hive-style partitioning and sidecar manifests as a data-lineage system for 31K+ committed market-data files.
- Built a full strategy-governance lifecycle — auto-promotion/demotion with fail-closed evidence requirements, a 5-rung drawdown governance ladder with anti-flapping cooldowns, and advisory-lock-guarded idempotent capital reallocation — driving strategies between research, paper, and live states.
- Engineered a deterministic simulation stack (settled/unsettled/reserved cash, T+N settlement, seeded stochastic fills, byte-identical replay as a CI regression gate) sharing TWAP/VWAP execution-policy code with the live path, plus a closed-loop slippage calibrator fed by realized paper-trading fills.
- Built a staged strategy-research funnel — registry-driven parameter-space candidate generation (grid/random/mutation, plus an ~854-skeleton composite-rule assembler) feeding cheap-simulation → full-window → walk-forward (train+test fold gating) → seed-resampled Monte Carlo stages with explicit Sharpe/drawdown/trade-count filters between stages — with SHA-256-derived deterministic seeds, lineage-hashed simulation caching, and checkpoint/resume for restartable experiment batches.
- Maintained 4,080 tests (SQLite-emulating-Postgres unit tier + dockerized-Postgres CI tier) with near-zero skip/xfail debt, including a schema-drift suite that diffs every Pydantic contract against its ORM model on every run.

---

## 8.5 CS concepts demonstrated (interview mapping)

Use this as the "concepts demonstrated" section of the portfolio page and as talking-points prep. The point is translation, not addition: the project already embodies these concepts under domain names — say them in the vocabulary interviewers listen for.

| CS concept interviewers probe | Where the repo already demonstrates it |
|---|---|
| State machines | Order lifecycle with full transition-matrix test (`execution/services/order_state_machine_service.py`); governance FSM (`governance/` deployment package); 5-rung drawdown ladder (migration `gg77hh88ii99`) |
| Idempotency / exactly-once semantics | SHA-256 order keys (`safety/services/order_idempotency_service.py`); config-hash universe rotation (`universe/services/universe_rotation_service.py`); checkpointed ingestion with retry caps + stale-lock reclaim (`ingestion/market_data/services/`) |
| Concurrency control | `pg_try_advisory_xact_lock` in reallocation (`application/services/quality_based_reallocation_service.py`); SAVEPOINT + IntegrityError race resolution (`storage/sor/repositories/core/position_snapshot_repository.py`); real thread-based throttle/sleeper tests (`tests/safety/test_order_throttle_service.py`, `tests/runtime/test_interruptible_sleeper.py`) |
| Transactions & atomicity | `SorUnitOfWork` (`storage/sor/unit_of_work.py`) — plus its known gap (30 of ~64 repos wired), which makes a stronger interview answer than pretending it's complete |
| Determinism & reproducibility | Seeded-RNG isolation, byte-identical replay as a CI regression gate (`tests/research/simulation/test_deterministic_replay.py`), 22-field cache lineage keys (`research/cache/cache_identity.py`) |
| Numerical methods / linear algebra | Black-Litterman posterior via matrix inversion (`research/black_litterman/black_litterman_research_service.py`); hand-rolled projected-gradient + simplex-projection optimizer (`application/services/factor_neutralization_service.py`); HHI concentration (`risk/risk_engine.py`) |
| Type-driven design | `Money = Decimal`, `UTCDateTime` (`contracts/common/types.py`); generic `Rule[T]` validation engine (`contracts/validators/core.py`) |
| Fail-closed / defense-in-depth design | Four-gate chain (`safety/services/live_trading_gate_service.py:53-68`); DB CHECK constraints beneath service validation (migration `zz09aa21bb32`) |
| Schema evolution & drift detection | 89 Alembic migrations; contract↔ORM↔live-DB drift suite (`tests/storage/schema_drift/test_contract_model_schema_drift.py`) |

---

## 9. Portfolio page structure suggestion (v2 — 11 sections)

Supersedes the earlier 6-item outline (kept below in git history only). Each
section names its primary owner for cross-cutting mechanisms so nothing gets
duplicated or buried.

1. **Hero**: one-line pitch (§1) + the four-gate safety chain as the visual hook — it's one method, `live_trading_gate_service.py:53-68`, screenshot-able as real code.
2. **System overview** (placeholder — finalize last): a top-level flowchart (contracts → storage → application → interfaces, plus where research/scheduler/safety/governance sit relative to that spine) + one-line blurb and quick-link per downstream section. Don't lock this in until sections 3–10 have stable headings to link to.
3. **Safety system breakdown**: gate chain → DB-persisted kill switch (migration `uu54vv66ww07`) → shadow-mode suppression (`run_order_submission_job.py:217`) → pre-trade gross/symbol/sector limits → DB CHECK constraints on live promotion. 153 dedicated tests. One-line cross-reference to the drawdown ladder ("covered in Governance") rather than repeating its mechanics.
4. **Research / strategy research funnel**: candidate generation (grid/random/mutation, ~854-skeleton composite-rule assembler) → 4-stage funnel (cheap → intermediate → walk-forward → Monte Carlo) → explicit filter thresholds → the concrete `ma_sweep_staged.yaml` scale example (9 variants × 9 folds × 50 MC runs). Lookahead/survivorship guards and deterministic replay live here too — this is "why naive backtests lie and how this platform avoids it."
5. **Governance & capital-allocation lifecycle**: auto promotion/demotion (fail-closed `source_run_id`), advisory-lock-guarded reallocation, append-only audit log with supersession chains. **Primary owner of the 5-rung drawdown ladder** (migration `gg77hh88ii99`) and the STORY-29 risk stack — cross-referenced, not repeated, from Safety. Also the funnel exit seam: `run_experiment_pipeline_cycle.py` auto-seeding survivors as `approved_research`, the literal handoff from section 4.
6. **Portfolio construction & intelligence**: from-scratch Black-Litterman (CAPM-verified), hand-rolled mean-variance optimizer / factor-neutralization (projected-gradient + simplex projection), and the `intelligence/` package (candidate ranking, overfitting/robustness estimation, regime-similarity cosine scoring, from-scratch agglomerative clustering) — preserve the package's own "does NOT promote strategies / enable trading" framing. Split further into two sections later if it drafts too dense (math vs. ranking/intelligence).
7. **Dataset layer & feature engineering**: versioned/checksummed/append-once Parquet layer, universe governance (propose→retire→activate rotation, survivorship guard, PIT membership), ingestion pipeline, feature-pipeline lineage guard (`MixedLineageError` blocking raw/adjusted mixing). 31K+ committed Parquet files as the scale hook.
8. **Testing suite**: 4,080 tests / 362 files / 86,651 LOC, schema-drift suite (28 contract/ORM pairs diffed every run), 153-test safety directory, 8 layered scenario builders, dockerized-Postgres CI tier + SQLite unit tier, near-zero skip/xfail debt. Pulls straight from §6/§11 — no new material needed.
9. **Backtesting & simulation engine** (segues into 10): settled/unsettled/reserved cash tracking, T+N settlement, seeded-RNG stochastic fills, byte-identical replay as a CI gate, sim/live parity via shared TWAP/VWAP code, closed-loop slippage calibration. End by pointing at "here's what a real run through this engine produces."
10. **Visualization/storytelling — 2-year backtest results (when finished)** — **currently blocked, not just pending**: every committed backtest artifact shows `total_orders=0` and the 26 committed PNGs are self-labeled synthetic GBM data (§10 item 17). Needs an actual end-to-end run through section 9's engine before this section can be written honestly. Decide whether to ship the page with a "coming soon" placeholder here rather than blocking the whole page on it.
11. **What's next / closing**: §10 "Honest gaps," reused near-verbatim — already ordered by discovery risk. Link the §8.5 CS-concepts interview-mapping table here as an optional "interview prep" appendix rather than a main-page section (it's meta content about discussing the project, not project content itself).

---

## 10. Honest gaps / what's next

Ordered roughly by "an interviewer will find this in five minutes" risk:

**Fix before publishing**
1. Frontend build break: `src/lib/utils.ts` missing, imported by 9 files. Also `.husky/pre-commit` runs a nonexistent `npm test`.
2. `README.md` and `CHANGELOG.md` are stuck at "Phase 5" and **factually wrong about the kill switch** (they call it an internal stub with durable storage "deferred"; migration `uu54vv66ww07` shipped a DB-backed table). Months of shipped work — drawdown ladder, shadow validation, factor/BL/portfolio-construction stack — have no changelog entries. Same staleness in `docs/backend/safety/safety.md` and `docs/backend/runtime/failure-modes.md` ("in-memory only").
3. CLAUDE.md fixes: frontend is not a mockup; the three `docs/architecture/` references should point at `docs/archived-docs/`; MACD isn't a strategy.
4. `mkdocs.yml` is fully broken (all 9 nav entries 404; ~86 of 150 docs have no nav coverage). Either fix the nav or delete the file.
5. `Dockerfile` fails to build as written (`COPY src/requirements.txt` — file lives at repo root) and its CMD is a placeholder.
6. Committed scratch: `.task_progress.json` (contains a failed run), `.runtime/`, `.tmp/`, `artifacts/backtesting/` (20 timestamped dev dumps), a committed *failing* soak report (`artifacts/operations/soak_report_test.json`), duplicate JSON exports.

**Known bugs found by this audit (fix or be ready to discuss)**
7. `strategy_health_lifecycle_service._persist_state` hardcodes `evaluation_count=1`, so the min-observation-cycles gate can never be satisfied in alert/enforce mode (latent — default mode is observe).
8. `governance_audit_routes.py::supersede_governance_audit_event` — `getattr(auth, "sub", "operator")` on a plain-str auth value: actor attribution on /supersede is always "operator".
9. `portfolio_routes.py`: zero RBAC on all 12 endpoints + broad `except Exception: pass` around the Alpaca fallback.
10. Storage layer: `TickerLifecycleRepository.upsert()` commits inside the shared UoW transaction; two silent no-op upserts (`ShadowRunRepository.update`, `StrategyRuntimeStateRepository.upsert`); `MarketBarRepository.to_contract()` drops persisted quality flags on every read.
11. `trading_freeze_service.py` is a print-only stub (real halting lives in the kill-switch path); `shadow_runtime_validation_service._hydrate_comparison_results` returns empty lists; two stale hardcoded US-holiday calendars (2025/2026 cutoffs) in `universe/market_calendar_service.py` and `ingestion/market_backfill_service.py`; late 5-minute bars are audit-logged but silently dropped.

**Architecture debt worth naming yourself**
12. `cli/commands/backtesting.py`: 2,400 lines of verification harness in production `src/` behind a misleading "[DEPRECATED]" label that five other modules depend on; one verify command toggles the real kill switch.
13. Dead code inventory: top-level `backtesting/` package, `BacktestReplayOrchestrator`, the `UniverseSnapshot` model/repo/service chain (explicitly deprecated but live), `frontend/src/mock/data.ts`, `StrategyLab.tsx`, the in-memory `safety/repositories/kill_switch_repository.py`.
14. Dual state machines / dual writers: two strategy-health classifiers writing the same table; kill-switch state writable via two services across two tables with no reconciliation; three independent "composite score" formulas.
15. Only 4 of 52 scheduler files are wired into Airflow DAGs; 9 of 13 registered jobs have no DAG. The STORY-29 risk stack runs each cycle with 4 permanently-`None` inputs (several risk checks silently no-op).
16. No lint/type-check job in CI (pre-commit only), and CI installs from pyproject floors rather than the lockfiles — CI isn't actually pinned.
17. All committed artifacts show zero real trading (`total_orders=0` in 32/32 platform-backtest artifacts; the 26 PNG charts are labeled synthetic GBM data — the self-disclosure is genuinely good, but be ready to say "the platform has not run a real money-making backtest end-to-end in committed history").
18. Test gaps: universe-rotation atomicity on partial failure is untested (rotation tests use in-memory stubs, not transactional sessions); execution policy modules (TWAP/VWAP slicers, order-type resolver) and several scheduler cycle runners have zero direct test references; frontend has zero tests; the valuable ORM-vs-migrated-DB half of the schema-drift suite is integration-gated and skipped in default runs.

---

## 11. Coverage report

Phase 0 enumerated **33,256 tracked files** (`git ls-files | wc -l`), of which **31,475 are binary Parquet market-data files under `data/`** — excluded from reading as binary data assets. Auditable scope: **1,781 files**. Per-file findings live in `audit/raw/` (23 files, 6,317 lines).

| Directory | Files (tracked) | Read | Coverage | Skips & reasons |
|---|---|---|---|---|
| `src/…/contracts/` | 92 | 92 | 100% | — |
| `src/…/storage/` | 170 | 170 | 100% | — |
| `src/…/application/` | 71 | 71 | 100% | — |
| `src/…/interfaces/` + `api/` + `config/` + `common/` | 50 | 50 | 100% | — |
| `src/…/observability/` + `platform/` + `governance/` | 32 | 32 | 100% | — |
| `src/…/research/` | 130 | 130 | 100% | — |
| `src/…/strategy/` | 62 | 62 | 100% | — |
| `src/…/execution/` + `risk/` + `portfolio/` | 61 | 61 | 100% | — |
| `src/…/scheduler/` + `backtesting/` | 59 | 59 | 100% | 14 `__init__.py` verified 0-byte via `wc -l` (no content to read) |
| `src/…/safety/` + `runtime/` | 45 | 45 | 100% | — |
| `src/…/universe/` + `ingestion/` + `feature_engineering/` | 84 | 84 | 100% | 19 `__init__.py` verified empty, noted collectively |
| `src/…/cli/` | 25 | 25 | 100% | — |
| `tests/` (all) | 362 | 362 | 100% | — |
| `frontend/` | 59 | 58 | 98% | `package-lock.json` used for version verification only, not read line-by-line (lockfile) |
| `docs/` | 150 | 150 | 100% | — |
| `infra/` (incl. 89 migrations) | 112 | 112 | 100% | all 89 migrations opened individually |
| `scripts/` + `experiments/` + `orchestration/` + `.github/` + root configs | 25 | 25 | 100% | README/CHANGELOG read via full-structure + targeted section reads (590/906 lines) |
| `visualization/` + `artifacts/` + `fixtures/` | 155 | 129 | 83% | 26 `.png` listed-not-read (binary images); 23 of 29 YAMLs cross-checked against their README config index rather than opened individually (per depth calibration; 6 opened in full) |
| `data/` | 31,475 | 0 | excluded | binary Parquet data assets |
| `.idea/` | 24 | 0 | excluded | IDE metadata |
| misc root (`trading_platform_screens.html`, `.task_progress.json`, `.runtime`, `.tmp`, `PORTFOLIO_NOTES.md`, `.pre-commit-config.yaml` etc.) | ~10 | ~10 | 100% | `trading_platform_screens.html` characterized (visual reference), not line-audited |

**Bottom line: 1,727 of 1,781 auditable files read in full (97%); the 54 not fully read are 26 binary PNGs, 23 README-cross-checked fixture YAMLs, 1 lockfile, and ~4 partially-read jumbo prose files — zero source-code files were sampled or skipped.** All numbers in this document trace to commands whose output is preserved in `audit/01_metrics.txt` or in the header sections of the relevant `audit/raw/*.md` file.
