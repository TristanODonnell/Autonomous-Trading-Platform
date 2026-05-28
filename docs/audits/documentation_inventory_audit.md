# Documentation Inventory Audit

## Executive Summary

This audit covers 51 markdown files found outside `docs/archived-docs/**`: root-level repository docs, `docs/**/*.md`, markdown files under `src/**`, the frontend README, and the Alembic command note under `infra/**`.

The documentation has three competing shapes:

- A newer domain/reference layer under `docs/architecture/`, `docs/domains/`, `docs/orchestration/`, and `docs/cli/`.
- Large audit and implementation-history documents that contain useful backend knowledge but are not clearly separated from canonical reference material.
- Root and tool-context files (`README.md`, `CLAUDE.md`, `CLAUDE_frontend_previous_story.md`) that point to missing or stale canonical docs and duplicate operational setup guidance.

The strongest existing canonical anchors are `docs/architecture/system-overview.md`, `docs/architecture/layering.md`, `docs/architecture/data-flow.md`, the `docs/domains/*.md` files, `docs/orchestration/trading-cycle.md`, `docs/orchestration/ingestion-cycle.md`, and `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`. The largest cleanup opportunity is to split audit/remediation material from durable domain references, then move source-of-truth docs into predictable domain folders.

Important caveat: this is a documentation-only audit. I did not verify claims against code beyond using file paths and headings present in the docs.

## Methodology

- Enumerated markdown files with `rg --files -g "*.md" -g "!docs/archived-docs/**"`.
- Read headings and representative content from architecture, domain, orchestration, CLI, observability, and root docs.
- Treated files with empty content or missing headings as unclear/stale candidates.
- Classified docs by apparent purpose and usefulness from content, file location, headings, stated status, and cross-references.
- Did not edit, move, delete, rename, or rewrite existing documentation or code.

## Documentation Inventory

| File | Domain | Type | Current Usefulness | Notes |
|---|---|---|---|---|
| `README.md` | Root onboarding, phase history | Mixed reference plus implementation summary | Medium, stale in places | Good setup basics and historical phase notes. Its "Canonical Docs" list points to files not present in this repo snapshot, such as `docs/v1-boundaries.md`, `docs/safety-doctrine.md`, and `docs/storage/index.md`. |
| `CHANGELOG.md` | Release/phase history | Implementation summary / changelog | Medium | Useful historical phase record through safety/risk implementation. Overlaps heavily with the long implementation sections in `README.md`. |
| `CLAUDE.md` | Agent/developer context | Operational reference / tool context | Medium, stale in places | Contains commands, architecture summary, frontend rules, infra ports. References missing docs under `docs/architecture/v1-boundaries.md`, `docs/architecture/safety-doctrine.md`, `docs/architecture/invariants.md`, and `docs/storage/`. |
| `CLAUDE_frontend_previous_story.md` | Frontend context | Historical frontend implementation note | Low to medium | Useful for frontend design history, but appears superseded by `CLAUDE.md` and frontend code state. Backend-facing only through "do not touch backend" guidance. |
| `frontend/README.md` | Frontend tooling | Generated Vite reference | Low | Generic React + TypeScript + Vite README. Not repository-specific enough to be canonical. |
| `infra/db/alembic/commands.md` | Database migrations | Operations note | Low to medium | Short Alembic troubleshooting note. Should be folded into a DB operations guide. |
| `docs/architecture/system-overview.md` | Backend architecture overview | Current reference documentation | High | Strong canonical candidate. Covers runtime shape, domains, persistence, safety, reproducibility, and known gaps. |
| `docs/architecture/layering.md` | Backend layering | Current reference documentation | High | Strong canonical architecture doc for dependency direction and ownership boundaries. |
| `docs/architecture/data-flow.md` | Runtime/data flow | Current reference documentation | High | Strong canonical candidate for end-to-end ingestion, storage, trading cycle, safety, execution, reconciliation, and audit flow. |
| `docs/architecture/strategy_registry.md` | Strategy metadata | Current reference documentation | High | Detailed registry reference, including metadata, dependencies, generation relationship, and adding strategies. |
| `docs/architecture/strategy_generation_engine.md` | Strategy generation | Current reference documentation | High | Explains generation methods, determinism, deduplication, compatibility filtering, and CLI tooling. |
| `docs/architecture/component_registry.md` | Strategy components | Current reference documentation | High | Defines component registry and its relationship to strategy generation and composite strategies. |
| `docs/architecture/composite_rule_strategy.md` | Strategy composition | Current reference documentation | High | Detailed composite rule strategy reference with config shape, flow, examples, warmup, and feature dependency aggregation. |
| `docs/architecture/indicator_vs_feature_architecture.md` | Features vs indicators | Current reference with audit notes | High | Useful conceptual boundary doc for strategy indicators, persisted features, dependency metadata, and simulation feature flow. |
| `docs/architecture/feature_dependency_resolution.md` | Feature dependency lineage | Current reference documentation | High | Strong canonical candidate for feature dependency resolution, lineage validation, metadata persistence, warmup, and regime dataset addition. |
| `docs/architecture/feature_dependency_integration_audit.md` | Feature dependency integration | Audit/remediation plan | Medium | Useful task audit and implementation plan. Overlaps with `feature_dependency_resolution.md` and `indicator_vs_feature_architecture.md`; should become historical note after verified completion. |
| `docs/architecture/research_strategy_audit.md` | Research and strategy architecture | Audit/remediation plan | Medium | Broad, detailed audit of folder map, strategy layer, experiment funnel, gaps, test coverage, and roadmap. Some path descriptions are explicitly superseded by `research_execution_paths.md`. |
| `docs/architecture/research_execution_paths.md` | Research/replay/backtest paths | Current classification audit | High | Explicitly says it supersedes informal path descriptions in `research_strategy_audit.md`. Strong canonical input for research/simulation overview. |
| `docs/architecture/research_caching.md` | Research caching | Current reference documentation | High | Good reference for cache keys, lookup protocol, lineage-safe validation, provenance, persistence, CLI, and invariants. |
| `docs/architecture/parallel_research_execution.md` | Research parallelism | Current reference documentation | High | Covers parallelizable boundaries, execution model, determinism, cache/checkpoint/artifact safety, and failure behavior. |
| `docs/research_checkpoint_resume.md` | Research checkpointing | Current reference documentation | High | Focused reference for resumable units, restart semantics, CLI, and parallel execution notes. |
| `docs/architecture/research_orchestration_observability_audit.md` | Research observability | Audit/remediation plan | Medium | High-value audit dated 2026-05-22. Should feed observability and research canonical docs, then move to audits or implementation notes. |
| `docs/architecture/advanced_validation_framework.md` | Research validation | Current reference documentation | High | Covers robustness scoring, walk-forward validation, stress testing, survivorship-safe validation, overfitting, sensitivity, persistence, CLI, limitations, and future ML direction. |
| `docs/architecture/market_regime_classification.md` | Regime features | Current reference documentation | High | Good canonical material for regime dimensions, persisted dataset, pipeline, strategy integration, code organization, lineage, limitations. |
| `docs/architecture/regime_conditioned_analysis.md` | Regime analysis | Current reference documentation | High | Covers regime joins, metrics, profiles, transitions, persistence, SimulationRunner integration, CLI, limitations. |
| `docs/architecture/ml_assisted_research.md` | ML-assisted research | Current reference / future-facing design | Medium to high | Good design doc for ranking, clustering, robustness, overfitting, regime-aware intelligence, artifacts, CLI. Some sections are deferred/future-oriented. |
| `docs/architecture/execution_policy_simulation_parity.md` | Execution simulation parity | Implementation summary / reference | High | Covers TASK D-01, `IExecutionModel`, simulation policy behavior, traceability, determinism, runtime separation, integration point, key files. |
| `docs/architecture/broker_event_stream_and_order_lifecycle.md` | Broker/order lifecycle | Implementation summary / reference | High | Covers E-01/E-02, websocket event stream, order states, transitions, idempotency, reconnect, 404/expiry handling, traceability. |
| `docs/architecture/portfolio_governance_allocation_audit.md` | Portfolio/governance/allocation | Audit/remediation plan | Medium | Very detailed audit, gap analysis, failure matrix, maturity assessment, roadmap, and key file inventory. Contains stale risk because findings include implementation-state claims that may now have changed. |
| `docs/audits/execution_simulation_audit.md` | Execution/simulation realism | Audit/remediation plan | Medium | Detailed audit with realism, failure, divergence, determinism, accounting, event ordering, calibration findings and remediation tiers. Several findings appear related to docs later written under `docs/architecture/`. |
| `docs/domains/contracts.md` | Contracts | Current reference documentation | High | Domain overview for contract groups, invariants, implementation notes, and limitations. |
| `docs/domains/ingestion.md` | Ingestion/corporate actions | Current reference documentation | High | Covers sources, pipeline, validation, late/missing/outlier policy, corporate actions, observability, limitations. |
| `docs/domains/storage.md` | Storage/versioning | Current reference documentation | High | Covers Postgres SoR, Parquet datasets, dataset/universe versioning, audit logging, current behavior, limitations. |
| `docs/domains/universe.md` | Universe governance | Current reference documentation | High | Covers selection, snapshots, lifecycle, guarantees, current behavior, limitations. |
| `docs/domains/strategy.md` | Strategy runtime | Current reference documentation | High | Covers decision flow, lifecycle, intended invariants, current behavior, limitations. |
| `docs/domains/execution.md` | Execution/reconciliation | Current reference documentation | High | Detailed doc for order flow, state machine, fills, reconciliation, retry idempotency, fill-quality analytics, out-of-order protection, current behavior, limitations. |
| `docs/domains/safety.md` | Safety/risk controls | Current reference documentation | High | Covers environment model, live enablement, idempotency, caps/throttles, broker account allowlist, shadow mode, behavior, limitations. |
| `docs/domains/scheduler.md` | Scheduler/orchestration | Current reference documentation | High | Covers runtime cadence, trading cycle orchestration, behavior, Airflow/scheduled entry points, limitations. |
| `docs/domains/research.md` | Research domain | Placeholder / roadmap | Low to medium | Short and mostly planned responsibilities. Should be expanded or replaced by a canonical research overview sourced from richer architecture docs. |
| `docs/domains/backtesting.md` | Backtesting domain | Placeholder / roadmap | Low to medium | Short and mostly planned responsibilities. Should be reconciled with `research_execution_paths.md` and execution/simulation docs. |
| `docs/orchestration/trading-cycle.md` | Runtime cycle | Current reference documentation | High | Good canonical input for runtime cycle overview. Covers trigger, cadence, current step order, step details, degraded/failure behavior, limitations. |
| `docs/orchestration/ingestion-cycle.md` | Ingestion cycle | Current reference documentation | High | Good canonical input for ingestion. Covers current flow, fetch/aggregation, validation, corporate actions, event logging, SLA, limitations. |
| `docs/orchestration/failure-modes.md` | Runtime failures | Current reference documentation | High | Covers readiness misses, order submission errors, invalid transitions, reconciliation mismatch, freeze, kill switch, runtime gates, scheduler handling, operator response. |
| `docs/cli/strategy_generation.md` | Strategy generation CLI | Current reference documentation | Medium to high | Focused CLI doc for strategy generation. Overlaps with `strategy_generation_engine.md` and the large runtime harness reference. |
| `docs/interfaces/cli.md` | CLI interface | Empty/placeholder | Low | File exists but content read as empty. Should be populated from CLI docs or removed/archived later after verification. |
| `docs/operations/runbooks.md` | Operations | Empty/placeholder | Low | File exists but content read as empty. Should become the operator runbook index or be archived if intentionally unused. |
| `docs/operations/debugging.md` | Operations/debugging | Empty/placeholder | Low | File exists but content read as empty. Should become a debugging guide sourced from CLI harness workflows or be archived. |
| `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md` | CLI/runtime operations | Current reference plus audit recommendations | High | Large operator handbook for 56 CLI commands across 12 domains. Strong canonical input, but should move under `docs/backend/cli/` or `docs/operations/`. |
| `src/autonomous_trading_platform/storage/sor/docs/template.md` | Storage docs template | Template / unclear | Low | Appears to be a template, not user-facing documentation. Should be kept near code only if actively used for generated docs. |
| `src/autonomous_trading_platform/observability/docs/instrumentation_inventory.md` | Observability | Current inventory / audit | High | Detailed instrumentation inventory with telemetry map, metrics, logging, tracing, persistence, health, CLI/runner entry points, LGTM config. Should feed canonical observability docs. |
| `src/autonomous_trading_platform/observability/docs/correlation_conventions.md` | Observability correlation | Current reference documentation | High | Concise source for correlation ID conventions, Tempo/Loki links, and Prometheus cardinality rule. |
| `src/autonomous_trading_platform/observability/docs/alerting.md` | Observability alerting | Current reference documentation | High | Concise alerting/operator response reference covering severity, provisioning, APIs, audit linkage, and advisory controls. |
| `src/autonomous_trading_platform/research/experiments/frontend_input_mapping.md` | Frontend/research integration | API/dashboard integration reference | Medium | Maps frontend experiment inputs to backend research experiment configuration. Should live under API/dashboard or research integration docs. |

## Domain Buckets

### Backend Architecture Overview

Canonical candidates: `docs/architecture/system-overview.md`, `docs/architecture/layering.md`, `docs/architecture/data-flow.md`.

These describe the layered backend, major domains, dependency direction, runtime flow, persistence, safety, audit, and known gaps. They should remain concise and point into domain-specific docs rather than duplicating every subsystem.

### CLI / Operations / Runtime Commands

Relevant files: `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`, `docs/cli/strategy_generation.md`, `infra/db/alembic/commands.md`, `docs/interfaces/cli.md`, `docs/operations/runbooks.md`, `docs/operations/debugging.md`, `CLAUDE.md`, `README.md`.

The CLI harness reference is the most complete source. `docs/interfaces/cli.md`, `docs/operations/runbooks.md`, and `docs/operations/debugging.md` are currently placeholders or empty reads and should become indexes or be archived.

### Research / Experiments / Simulation

Relevant files: `docs/architecture/research_strategy_audit.md`, `docs/architecture/research_execution_paths.md`, `docs/architecture/research_caching.md`, `docs/architecture/parallel_research_execution.md`, `docs/research_checkpoint_resume.md`, `docs/architecture/advanced_validation_framework.md`, `docs/architecture/market_regime_classification.md`, `docs/architecture/regime_conditioned_analysis.md`, `docs/architecture/ml_assisted_research.md`, `docs/domains/research.md`, `docs/domains/backtesting.md`, `src/autonomous_trading_platform/research/experiments/frontend_input_mapping.md`.

This is the densest backend documentation area. It needs a canonical research overview that separates simulation paths, experiment orchestration, validation, caching/checkpointing, regime analysis, ML assistance, and frontend/API inputs.

### Execution / Fill Modeling / Trading Runtime

Relevant files: `docs/domains/execution.md`, `docs/architecture/execution_policy_simulation_parity.md`, `docs/architecture/broker_event_stream_and_order_lifecycle.md`, `docs/audits/execution_simulation_audit.md`, `docs/orchestration/trading-cycle.md`, `docs/architecture/data-flow.md`.

`docs/domains/execution.md` is the best current execution reference. Audit findings in `execution_simulation_audit.md` should be reconciled against implemented docs like `execution_policy_simulation_parity.md` and `broker_event_stream_and_order_lifecycle.md`.

### Portfolio / Allocation / Governance

Relevant files: `docs/architecture/portfolio_governance_allocation_audit.md`, `docs/domains/strategy.md`, `docs/domains/safety.md`, `src/autonomous_trading_platform/observability/docs/instrumentation_inventory.md`.

The portfolio/governance/allocation content is mostly in one large audit. There is no concise canonical portfolio/governance overview yet.

### Risk / Controls / Kill Switch / Runtime Safety

Relevant files: `docs/domains/safety.md`, `docs/orchestration/failure-modes.md`, `docs/architecture/system-overview.md`, `docs/architecture/portfolio_governance_allocation_audit.md`, `CLAUDE.md`, `README.md`.

`docs/domains/safety.md` is the strongest current canonical doc. It should be updated later after code verification because active worktree changes include kill-switch persistence-related files.

### Dataset Versioning / Lineage / Ingestion / Features

Relevant files: `docs/domains/storage.md`, `docs/domains/ingestion.md`, `docs/architecture/feature_dependency_resolution.md`, `docs/architecture/indicator_vs_feature_architecture.md`, `docs/architecture/feature_dependency_integration_audit.md`, `docs/architecture/market_regime_classification.md`, `docs/orchestration/ingestion-cycle.md`, `docs/architecture/data-flow.md`.

There is strong material, but it is split between storage, ingestion, feature dependency docs, and regime classification docs. A dataset lineage overview should unify these.

### Corporate Actions / Adjusted Data

Relevant files: `docs/domains/ingestion.md`, `docs/orchestration/ingestion-cycle.md`, `docs/domains/storage.md`, `docs/architecture/data-flow.md`, `src/autonomous_trading_platform/observability/docs/instrumentation_inventory.md`.

Corporate actions are documented as part of ingestion and storage but do not have their own canonical adjusted-data doc. If adjusted data is a core feature, this should become a small dedicated doc.

### Observability / Runtime Soak / Verification

Relevant files: `src/autonomous_trading_platform/observability/docs/instrumentation_inventory.md`, `src/autonomous_trading_platform/observability/docs/correlation_conventions.md`, `src/autonomous_trading_platform/observability/docs/alerting.md`, `docs/architecture/research_orchestration_observability_audit.md`, `docs/orchestration/failure-modes.md`, `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`.

The observability docs are useful but live under `src/`, not `docs/`. They should feed a canonical `docs/backend/observability/` area.

### Broker / Paper Trading / Reconciliation

Relevant files: `docs/architecture/broker_event_stream_and_order_lifecycle.md`, `docs/domains/execution.md`, `docs/audits/execution_simulation_audit.md`, `docs/orchestration/failure-modes.md`, `docs/architecture/data-flow.md`, `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`.

Current docs cover broker events, order states, reconciliation, idempotency, and drift. These should be consolidated into an execution/broker runtime canonical doc.

### REST API / Dashboard Integration

Relevant files: `src/autonomous_trading_platform/research/experiments/frontend_input_mapping.md`, `src/autonomous_trading_platform/observability/docs/alerting.md`, `src/autonomous_trading_platform/observability/docs/correlation_conventions.md`, `CLAUDE.md`, `CLAUDE_frontend_previous_story.md`.

This area is under-documented. Existing material mentions frontend mock status, alert APIs, runtime API rows, and experiment input mapping, but there is no canonical API/dashboard contract overview.

### Frontend Integration Docs

Relevant files: `CLAUDE.md`, `CLAUDE_frontend_previous_story.md`, `frontend/README.md`, `src/autonomous_trading_platform/research/experiments/frontend_input_mapping.md`.

The frontend-specific docs are scattered between root agent context, generic Vite README, and one backend experiment mapping doc. Backend-facing frontend integration should live separately from frontend implementation history.

### Roadmaps / Audits / Remediation Plans

Relevant files: `docs/audits/execution_simulation_audit.md`, `docs/architecture/research_strategy_audit.md`, `docs/architecture/research_orchestration_observability_audit.md`, `docs/architecture/portfolio_governance_allocation_audit.md`, `docs/architecture/feature_dependency_integration_audit.md`, large recommendation sections in `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`.

These docs should be grouped under `docs/audits/` or `docs/implementation-summaries/` after canonical content has been extracted.

### Historical / Archive Candidates

Likely candidates: `CLAUDE_frontend_previous_story.md`, `frontend/README.md`, empty placeholders if unused, superseded sections in `README.md`, superseded path descriptions in `research_strategy_audit.md`, completed task audits after verification.

## Redundancy / Overlap Findings

### Architecture overview cluster

Files: `docs/architecture/system-overview.md`, `docs/architecture/layering.md`, `docs/architecture/data-flow.md`, `README.md`, `CLAUDE.md`.

Overlap: backend domains, runtime shape, persistence, safety, orchestration, and known gaps.

Canonical candidate: `docs/architecture/system-overview.md` for high-level overview, with `layering.md` for dependency rules and `data-flow.md` for end-to-end movement of data and runtime state.

Recommendation: keep `README.md` focused on onboarding and link to the architecture docs. Keep `CLAUDE.md` as agent context only, with links to canonical docs instead of duplicating architecture details.

### Research path and strategy architecture cluster

Files: `docs/architecture/research_strategy_audit.md`, `docs/architecture/research_execution_paths.md`, `docs/domains/research.md`, `docs/domains/backtesting.md`, `docs/architecture/advanced_validation_framework.md`.

Overlap: research/backtest/replay entry points, experiment funnel, simulation runner role, validation, path classification, and gaps.

Canonical candidate: `docs/architecture/research_execution_paths.md` for path classification because it explicitly supersedes informal path descriptions in `research_strategy_audit.md`.

Recommendation: create one canonical research/simulation overview, expand or replace `docs/domains/research.md` and `docs/domains/backtesting.md`, and retain `research_strategy_audit.md` as historical audit material.

### Feature dependency and persisted feature cluster

Files: `docs/architecture/feature_dependency_resolution.md`, `docs/architecture/indicator_vs_feature_architecture.md`, `docs/architecture/feature_dependency_integration_audit.md`, `docs/architecture/strategy_registry.md`, `docs/architecture/composite_rule_strategy.md`, `docs/architecture/market_regime_classification.md`.

Overlap: strategy dependency metadata, indicator vs persisted feature boundary, lineage validation, warmup, simulation feature flow, regime dataset dependency.

Canonical candidate: `docs/architecture/feature_dependency_resolution.md`, with conceptual background from `indicator_vs_feature_architecture.md`.

Recommendation: keep `feature_dependency_integration_audit.md` as an implementation note after verifying TASK-2.1/TASK-2.2 status. Avoid repeating dependency-resolution rules in strategy registry/composite docs; link to the canonical feature dependency doc.

### Strategy generation and component cluster

Files: `docs/architecture/strategy_registry.md`, `docs/architecture/strategy_generation_engine.md`, `docs/architecture/component_registry.md`, `docs/architecture/composite_rule_strategy.md`, `docs/cli/strategy_generation.md`.

Overlap: generation tooling, registry metadata, component consumption, composite strategy generation, CLI commands.

Canonical candidate: split by purpose: `strategy_registry.md` for metadata, `strategy_generation_engine.md` for generation behavior, `docs/cli/strategy_generation.md` for command syntax.

Recommendation: create a `docs/backend/research/strategy-generation.md` index that links these docs and states ownership boundaries.

### Execution and simulation realism cluster

Files: `docs/audits/execution_simulation_audit.md`, `docs/architecture/execution_policy_simulation_parity.md`, `docs/architecture/broker_event_stream_and_order_lifecycle.md`, `docs/domains/execution.md`, `docs/orchestration/trading-cycle.md`.

Overlap: fill semantics, order lifecycle, broker updates, reconciliation, event ordering, idempotency, runtime vs simulation divergence.

Canonical candidate: `docs/domains/execution.md` for current execution behavior, supported by `execution_policy_simulation_parity.md` and `broker_event_stream_and_order_lifecycle.md`.

Recommendation: review each finding in `execution_simulation_audit.md` against current implementation and move resolved items into implementation summaries or archive.

### Safety, failure modes, and governance cluster

Files: `docs/domains/safety.md`, `docs/orchestration/failure-modes.md`, `docs/architecture/portfolio_governance_allocation_audit.md`, `README.md`, `CLAUDE.md`.

Overlap: environment gating, kill switch, runtime gates, freeze, shadow mode, safety controls, risk limits, governance state.

Canonical candidate: `docs/domains/safety.md` for safety controls; `docs/orchestration/failure-modes.md` for operational responses.

Recommendation: create a portfolio/governance overview and update safety docs after verifying kill-switch persistence and runtime-control state.

### Observability cluster

Files: `src/autonomous_trading_platform/observability/docs/instrumentation_inventory.md`, `src/autonomous_trading_platform/observability/docs/correlation_conventions.md`, `src/autonomous_trading_platform/observability/docs/alerting.md`, `docs/architecture/research_orchestration_observability_audit.md`, `docs/orchestration/failure-modes.md`.

Overlap: metrics, traces, logs, runtime jobs, correlation IDs, alerting, operator response, research instrumentation gaps.

Canonical candidate: `instrumentation_inventory.md` for current inventory, with `correlation_conventions.md` and `alerting.md` as focused references.

Recommendation: move or mirror these under `docs/backend/observability/` and keep research-specific audit findings under `docs/audits/`.

### CLI and operations cluster

Files: `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`, `docs/cli/strategy_generation.md`, `docs/interfaces/cli.md`, `docs/operations/runbooks.md`, `docs/operations/debugging.md`, `infra/db/alembic/commands.md`, `CLAUDE.md`.

Overlap: commands, runtime harnesses, debugging workflows, validation harnesses, migrations.

Canonical candidate: `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`.

Recommendation: move the operator handbook into `docs/backend/cli/` or `docs/operations/`, then use `docs/operations/runbooks.md` and `docs/operations/debugging.md` as real indexes.

## Stale or Unclear Documentation

| File | Why stale or unclear | Evidence | Recommended later action |
|---|---|---|---|
| `README.md` | Lists missing canonical docs and mixes setup with long historical implementation summaries. | "Canonical Docs" references paths not found in the inventory. Status says Phase 5 active while later docs cover research, observability, parity, and 2026 audits. | Keep setup, replace missing canonical links, move long phase history to changelog or implementation summaries. |
| `CLAUDE.md` | Contains stale canonical doc paths and possibly outdated frontend/mock guidance. | References missing `docs/architecture/v1-boundaries.md`, `safety-doctrine.md`, `invariants.md`, and `docs/storage/`; says frontend is static mockup/no real API calls. | Update after code/docs verification; keep as agent context and link canonical docs. |
| `CLAUDE_frontend_previous_story.md` | Appears historical and duplicated by `CLAUDE.md`. | Title says previous story; describes frontend folder and patterns independently of `frontend/README.md`. | Archive or move to frontend historical notes if still useful. |
| `frontend/README.md` | Generic generated Vite doc. | Contains generic React Compiler and ESLint expansion text, not project-specific app guidance. | Replace with project-specific frontend README or archive generated boilerplate. |
| `docs/interfaces/cli.md` | Empty/placeholder. | Reading file returned no content; no headings found in later content search. | Populate from CLI harness reference or archive if unused. |
| `docs/operations/runbooks.md` | Empty/placeholder. | Reading file returned no content; no headings found in later content search. | Populate as operations runbook index using trading cycle, failure modes, observability, CLI workflows. |
| `docs/operations/debugging.md` | Empty/placeholder. | Reading file returned no content; no headings found in later content search. | Populate from CLI "daily debugging" and failure-mode docs, or archive. |
| `docs/domains/research.md` | Thin placeholder compared with rich research architecture docs. | Headings are Overview, Current Status, Planned Responsibilities, Notes. | Replace with canonical research domain overview. |
| `docs/domains/backtesting.md` | Thin placeholder and likely superseded by detailed execution path audit. | Headings are Overview, Current Status, Planned Responsibilities, Notes. | Merge with research execution path canonical doc or expand into backtesting reference. |
| `docs/architecture/research_strategy_audit.md` | Some content explicitly superseded. | `research_execution_paths.md` says it supersedes informal path descriptions in this doc. | Retain as historical audit; mark superseded sections or extract still-current gaps. |
| `docs/architecture/feature_dependency_integration_audit.md` | Task-oriented implementation plan may be stale after implementation. | Contains `TASK-2.1 Implementation Plan`, recommended files, tests to add. | Verify task status, then convert to implementation note or archive. |
| `docs/audits/execution_simulation_audit.md` | Findings may be partially resolved by later implementation docs. | Later docs exist for D-01 parity, E-01/E-02 broker lifecycle, F-04/F-05 execution domain sections. | Reconcile findings against code and mark resolved/unresolved. |
| `docs/architecture/portfolio_governance_allocation_audit.md` | Findings may be stale and include mutable implementation-state claims. | Finding 03 says kill switch is in-memory and not persisted; current worktree includes uncommitted kill-switch persistence-related files. | Update only after code verification; keep as audit until resolved items are extracted. |
| `src/autonomous_trading_platform/storage/sor/docs/template.md` | Unclear audience and likely template-only. | Path and title indicate template, not reference documentation. | Keep near code if used by tooling; otherwise move to docs templates or archive. |

## Proposed Documentation Structure

```text
docs/
  README.md
  architecture/
    system-overview.md
    layering.md
    data-flow.md
  backend/
    cli/
    runtime/
    orchestration/
    research/
    simulation/
    execution/
    portfolio-governance/
    safety/
    ingestion/
    storage-lineage/
    corporate-actions/
    observability/
    broker/
    api/
  frontend/
  operations/
    runbooks/
    debugging/
  audits/
  roadmaps/
  implementation-summaries/
  templates/
  archived-docs/
```

### `docs/README.md`

Belongs here: documentation index, canonical source-of-truth list, doc placement rules.

Examples to feed it: root `README.md` canonical-doc section, this audit.

Type: canonical index.

### `docs/architecture/`

Belongs here: durable system-level architecture only.

Examples: `docs/architecture/system-overview.md`, `docs/architecture/layering.md`, `docs/architecture/data-flow.md`.

Type: canonical docs.

### `docs/backend/cli/`

Belongs here: argparse command structure, command decision tables, domain CLI references.

Examples: `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md`, `docs/cli/strategy_generation.md`, `docs/interfaces/cli.md`.

Type: canonical operations/reference docs, with deprecated command notes retained as implementation notes.

### `docs/backend/runtime/` and `docs/backend/orchestration/`

Belongs here: trading cycle, ingestion cycle, runtime states, scheduler behavior, failure boundaries.

Examples: `docs/orchestration/trading-cycle.md`, `docs/orchestration/ingestion-cycle.md`, `docs/orchestration/failure-modes.md`, `docs/domains/scheduler.md`.

Type: canonical docs.

### `docs/backend/research/` and `docs/backend/simulation/`

Belongs here: experiment orchestration, simulation runner, backtest/replay paths, validation, caching, checkpointing, parallel execution, regime analysis, ML-assisted research.

Examples: `research_execution_paths.md`, `research_caching.md`, `parallel_research_execution.md`, `research_checkpoint_resume.md`, `advanced_validation_framework.md`, `market_regime_classification.md`, `regime_conditioned_analysis.md`, `ml_assisted_research.md`.

Type: canonical docs plus future-facing design notes.

### `docs/backend/execution/` and `docs/backend/broker/`

Belongs here: order state machine, fill processing, execution policy parity, broker event stream, reconciliation, paper trading behavior.

Examples: `docs/domains/execution.md`, `execution_policy_simulation_parity.md`, `broker_event_stream_and_order_lifecycle.md`, relevant sections from `execution_simulation_audit.md`.

Type: canonical docs, with audits under `docs/audits/`.

### `docs/backend/portfolio-governance/`

Belongs here: portfolio construction, allocation, governance state, strategy health, capital allocation, rebalancing, portfolio-level risk.

Examples: `portfolio_governance_allocation_audit.md` as source material.

Type: new canonical docs plus historical audits.

### `docs/backend/safety/`

Belongs here: environment gating, kill switch, runtime gates, control state, shadow mode, risk caps, failure response.

Examples: `docs/domains/safety.md`, `docs/orchestration/failure-modes.md`, root README safety history.

Type: canonical docs.

### `docs/backend/ingestion/`, `docs/backend/storage-lineage/`, `docs/backend/corporate-actions/`

Belongs here: market data ingestion, validation, dataset versions, feature dependency resolution, lineage, adjusted data, universe snapshots.

Examples: `docs/domains/ingestion.md`, `docs/domains/storage.md`, `feature_dependency_resolution.md`, `indicator_vs_feature_architecture.md`, `market_regime_classification.md`, `ingestion-cycle.md`.

Type: canonical docs.

### `docs/backend/observability/`

Belongs here: instrumentation inventory, metrics/traces/logs conventions, alerting, correlation, runtime soak verification.

Examples: `src/autonomous_trading_platform/observability/docs/instrumentation_inventory.md`, `correlation_conventions.md`, `alerting.md`, `research_orchestration_observability_audit.md`.

Type: canonical docs plus audit notes.

### `docs/backend/api/`

Belongs here: REST API contracts used by dashboard/frontend, runtime API rows, alerts API, experiment input mapping.

Examples: `src/autonomous_trading_platform/research/experiments/frontend_input_mapping.md`, alert API section from `alerting.md`, frontend/API guidance from `CLAUDE.md`.

Type: canonical API/dashboard contract docs.

### `docs/frontend/`

Belongs here: frontend architecture, routing, state, design tokens, mock/API transition notes.

Examples: `CLAUDE_frontend_previous_story.md`, `frontend/README.md`, frontend sections of `CLAUDE.md`.

Type: canonical frontend docs and historical notes.

### `docs/operations/runbooks/` and `docs/operations/debugging/`

Belongs here: operator workflows, daily debugging, incident response, migrations, local infra, paper trading smoke checks.

Examples: CLI harness daily workflows, `infra/db/alembic/commands.md`, `docs/orchestration/failure-modes.md`.

Type: canonical runbooks.

### `docs/audits/`, `docs/roadmaps/`, `docs/implementation-summaries/`

Belongs here: dated audits, remediation plans, implementation summaries, completed task reports.

Examples: `execution_simulation_audit.md`, `research_strategy_audit.md`, `research_orchestration_observability_audit.md`, `feature_dependency_integration_audit.md`, `portfolio_governance_allocation_audit.md`, long historical sections from `README.md`.

Type: non-canonical historical or planning docs.

## Recommended Canonical Docs

### Backend Architecture Overview

Purpose: orient developers to system boundaries, domains, layering, persistence, runtime model, safety, and known gaps.

Should contain: domain map, dependency direction, runtime/persistence overview, source-of-truth links.

Feed from: `system-overview.md`, `layering.md`, `data-flow.md`, `CLAUDE.md`.

Missing sections: clear ownership table for newer domains such as portfolio governance, observability, API/dashboard integration.

### Runtime Cycle Overview

Purpose: source of truth for paper/live trading cycle order, scheduler entry points, degraded modes, and failure behavior.

Should contain: cycle trigger/cadence, step order, readiness checks, reconciliation, order submission, risk snapshot, run manifest/job rows, failure states.

Feed from: `trading-cycle.md`, `failure-modes.md`, `docs/domains/scheduler.md`, `data-flow.md`, CLI harness runtime sections.

Missing sections: explicit contract between runtime APIs, job rows, observability, and operator actions.

### Research / Simulation Overview

Purpose: explain which research, simulation, backtest, replay, and golden-path entry point to use.

Should contain: canonical path table, simulation runner, historical golden path, backtest orchestrator, debug replay, persistence differences, validation flow.

Feed from: `research_execution_paths.md`, `research_strategy_audit.md`, `advanced_validation_framework.md`, `docs/domains/research.md`, `docs/domains/backtesting.md`.

Missing sections: one current end-to-end diagram and explicit API/frontend handoff.

### Execution Realism Overview

Purpose: source of truth for execution modeling in simulation and runtime.

Should contain: fill model, slippage, execution policy parity, order state machine, partial/rejected/expired fills, deterministic behavior, accounting assumptions.

Feed from: `docs/domains/execution.md`, `execution_policy_simulation_parity.md`, `execution_simulation_audit.md`, `broker_event_stream_and_order_lifecycle.md`.

Missing sections: unresolved vs resolved audit findings from `execution_simulation_audit.md`.

### Portfolio / Governance Overview

Purpose: explain capital allocation, governance lifecycle, strategy promotion/demotion, health, rebalancing, and portfolio-level controls.

Should contain: governance state model, allocation rules, rebalance cadence, overrides, concentration limits, drawdown behavior, audit events.

Feed from: `portfolio_governance_allocation_audit.md`, `docs/domains/strategy.md`, `docs/domains/safety.md`, observability inventory governance rows.

Missing sections: concise current-state reference separate from the audit.

### Dataset Lineage Overview

Purpose: source of truth for dataset versions, feature dependencies, lineage validation, adjusted data, and universe snapshots.

Should contain: Parquet versioning, SoR relationships, feature dependency resolver, strategy dependency metadata, regime dataset lineage, corporate-action-adjusted data.

Feed from: `docs/domains/storage.md`, `docs/domains/ingestion.md`, `feature_dependency_resolution.md`, `indicator_vs_feature_architecture.md`, `market_regime_classification.md`, `ingestion-cycle.md`.

Missing sections: single lineage diagram across ingestion, features, simulations, and artifacts.

### Observability / Runtime Verification Overview

Purpose: source of truth for metrics, logs, traces, alerts, correlation, runtime soak verification, and dashboard drilldowns.

Should contain: telemetry map, correlation convention, alert lifecycle, runtime job rows, health endpoints, soak verification, research observability gaps.

Feed from: `instrumentation_inventory.md`, `correlation_conventions.md`, `alerting.md`, `research_orchestration_observability_audit.md`, CLI harness verification sections.

Missing sections: placement under `docs/` and explicit dashboard panel/API contract references.

### API / Dashboard Contract Overview

Purpose: explain backend contracts consumed by frontend/dashboard and how mock/frontend state maps to real APIs.

Should contain: REST route groups, runtime health rows, operations alerts, experiment input mapping, correlation drilldowns, mock-to-real transition rules.

Feed from: `frontend_input_mapping.md`, `alerting.md`, `correlation_conventions.md`, `CLAUDE.md`, `CLAUDE_frontend_previous_story.md`.

Missing sections: route-by-route dashboard contract inventory.

### CLI Operations Guide

Purpose: source of truth for what command to run, when, and why.

Should contain: command discovery, daily debugging, validation harnesses, runtime/scheduler operations, research/backfill commands, broker/paper trading commands, deprecated commands, safe-by-environment table.

Feed from: `CLI_RUNTIME_HARNESS_REFERENCE.md`, `docs/cli/strategy_generation.md`, `infra/db/alembic/commands.md`, empty operations placeholders.

Missing sections: split between canonical command reference and runbook workflows.

## Cleanup Roadmap

### Phase 1: Inventory and tag docs

- Add front matter or visible status tags to docs: `canonical`, `audit`, `implementation-summary`, `roadmap`, `historical`, `placeholder`.
- Start with the files flagged in this audit.
- Mark empty placeholder docs explicitly instead of leaving them ambiguous.

### Phase 2: Choose canonical docs

- Adopt the canonical docs listed above.
- Add a `docs/README.md` index that links only current source-of-truth docs and secondary historical docs.
- Remove missing canonical references from root `README.md` and `CLAUDE.md` after replacement docs exist.

### Phase 3: Merge redundant docs

- Merge research path material into a canonical research/simulation overview.
- Merge CLI docs into a proper CLI operations guide and runbook set.
- Merge feature dependency, lineage, and storage concepts into a dataset lineage overview with links back to detailed references.

### Phase 4: Archive stale docs

- Archive generated or historical frontend docs if no longer useful.
- Archive completed task audits after resolved findings are extracted.
- Archive or populate empty placeholders: `docs/interfaces/cli.md`, `docs/operations/runbooks.md`, `docs/operations/debugging.md`.

### Phase 5: Add README indexes per domain

- Add lightweight indexes under major folders: architecture, backend/research, backend/execution, backend/observability, operations, audits.
- Each index should list canonical docs first, then implementation notes, then historical audits.

### Phase 6: Enforce future doc placement rules

- New reference docs go under the owning backend/frontend/operations domain.
- New audits go under `docs/audits/` with a date, scope, status, and canonical docs affected.
- New implementation summaries go under `docs/implementation-summaries/`.
- Root docs should link to canonical docs rather than duplicating backend domain knowledge.

## Open Questions

- Which missing canonical docs referenced by `README.md` and `CLAUDE.md` were intentionally removed, and which should be restored from archive?
- Should observability docs remain colocated with code under `src/.../observability/docs/`, or should `docs/backend/observability/` become the public source of truth?
- Are active kill-switch persistence changes intended to obsolete findings in `portfolio_governance_allocation_audit.md` and safety limitations?
- Should frontend remain mock-only, as `CLAUDE.md` says, or has the REST/dashboard integration plan advanced?
- Are empty files under `docs/operations/` and `docs/interfaces/` intentional placeholders or accidental remnants?
- Should `README.md` retain phase implementation history, or should that content move entirely to `CHANGELOG.md` / `docs/implementation-summaries/`?
