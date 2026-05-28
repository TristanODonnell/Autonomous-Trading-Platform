# Documentation Migration Summary

## Files Moved

- `docs/documentation_inventory_audit.md` -> `docs/audits/documentation_inventory_audit.md`
- `docs/architecture/feature_dependency_integration_audit.md` -> `docs/audits/agent-findings/feature_dependency_integration_audit.md`
- `docs/architecture/portfolio_governance_allocation_audit.md` -> `docs/audits/agent-findings/portfolio_governance_allocation_audit.md`
- `docs/architecture/research_orchestration_observability_audit.md` -> `docs/audits/agent-findings/research_orchestration_observability_audit.md`
- `docs/architecture/research_strategy_audit.md` -> `docs/audits/agent-findings/research_strategy_audit.md`
- `docs/domains/contracts.md` -> `docs/backend/api/contracts.md`
- `src/autonomous_trading_platform/research/experiments/frontend_input_mapping.md` -> `docs/backend/api/frontend_experiment_input_mapping.md`
- `docs/architecture/broker_event_stream_and_order_lifecycle.md` -> `docs/backend/broker/broker_event_stream_and_order_lifecycle.md`
- `docs/interfaces/cli.md` -> `docs/backend/cli/cli.md`
- `src/autonomous_trading_platform/cli/CLI_RUNTIME_HARNESS_REFERENCE.md` -> `docs/backend/cli/runtime_harness_reference.md`
- `docs/cli/strategy_generation.md` -> `docs/backend/cli/strategy_generation.md`
- `docs/domains/execution.md` -> `docs/backend/execution/execution.md`
- `docs/architecture/execution_policy_simulation_parity.md` -> `docs/backend/execution/execution_policy_simulation_parity.md`
- `docs/domains/ingestion.md` -> `docs/backend/ingestion/ingestion.md`
- `src/autonomous_trading_platform/observability/docs/alerting.md` -> `docs/backend/observability/alerting.md`
- `src/autonomous_trading_platform/observability/docs/correlation_conventions.md` -> `docs/backend/observability/correlation_conventions.md`
- `src/autonomous_trading_platform/observability/docs/instrumentation_inventory.md` -> `docs/backend/observability/instrumentation_inventory.md`
- `docs/orchestration/ingestion-cycle.md` -> `docs/backend/orchestration/ingestion-cycle.md`
- `docs/domains/scheduler.md` -> `docs/backend/orchestration/scheduler.md`
- `docs/orchestration/trading-cycle.md` -> `docs/backend/orchestration/trading-cycle.md`
- `docs/architecture/advanced_validation_framework.md` -> `docs/backend/research/advanced_validation_framework.md`
- `docs/architecture/component_registry.md` -> `docs/backend/research/component_registry.md`
- `docs/architecture/composite_rule_strategy.md` -> `docs/backend/research/composite_rule_strategy.md`
- `docs/architecture/market_regime_classification.md` -> `docs/backend/research/market_regime_classification.md`
- `docs/architecture/ml_assisted_research.md` -> `docs/backend/research/ml_assisted_research.md`
- `docs/architecture/parallel_research_execution.md` -> `docs/backend/research/parallel_research_execution.md`
- `docs/architecture/regime_conditioned_analysis.md` -> `docs/backend/research/regime_conditioned_analysis.md`
- `docs/domains/research.md` -> `docs/backend/research/research.md`
- `docs/architecture/research_caching.md` -> `docs/backend/research/research_caching.md`
- `docs/research_checkpoint_resume.md` -> `docs/backend/research/research_checkpoint_resume.md`
- `docs/domains/strategy.md` -> `docs/backend/research/strategy.md`
- `docs/architecture/strategy_generation_engine.md` -> `docs/backend/research/strategy_generation_engine.md`
- `docs/architecture/strategy_registry.md` -> `docs/backend/research/strategy_registry.md`
- `docs/orchestration/failure-modes.md` -> `docs/backend/runtime/failure-modes.md`
- `docs/domains/safety.md` -> `docs/backend/safety/safety.md`
- `docs/domains/backtesting.md` -> `docs/backend/simulation/backtesting.md`
- `docs/architecture/research_execution_paths.md` -> `docs/backend/simulation/research_execution_paths.md`
- `docs/architecture/feature_dependency_resolution.md` -> `docs/backend/storage-lineage/feature_dependency_resolution.md`
- `docs/architecture/indicator_vs_feature_architecture.md` -> `docs/backend/storage-lineage/indicator_vs_feature_architecture.md`
- `docs/domains/storage.md` -> `docs/backend/storage-lineage/storage.md`
- `docs/domains/universe.md` -> `docs/backend/storage-lineage/universe.md`
- `CLAUDE_frontend_previous_story.md` -> `docs/frontend/claude_frontend_previous_story.md`
- `frontend/README.md` -> `docs/frontend/vite_readme.md`
- `docs/operations/debugging.md` -> `docs/operations/debugging/README.md`
- `infra/db/alembic/commands.md` -> `docs/operations/debugging/alembic_commands.md`
- `docs/operations/runbooks.md` -> `docs/operations/runbooks/README.md`
- `src/autonomous_trading_platform/storage/sor/docs/template.md` -> `docs/templates/sor_template.md`

## Files Left Unsorted

None.

Root-level `README.md`, `CHANGELOG.md`, and `CLAUDE.md` were left in place because they are conventional project/tooling entry points rather than backend domain docs.

## Files Identified as Canonical

Architecture:

- `docs/architecture/system-overview.md`
- `docs/architecture/layering.md`
- `docs/architecture/data-flow.md`

Backend API:

- `docs/backend/api/contracts.md`
- `docs/backend/api/frontend_experiment_input_mapping.md`

CLI and operations:

- `docs/backend/cli/runtime_harness_reference.md`
- `docs/backend/cli/strategy_generation.md`

Execution and broker:

- `docs/backend/execution/execution.md`
- `docs/backend/execution/execution_policy_simulation_parity.md`
- `docs/backend/broker/broker_event_stream_and_order_lifecycle.md`

Research and simulation:

- `docs/backend/research/strategy_registry.md`
- `docs/backend/research/strategy_generation_engine.md`
- `docs/backend/research/component_registry.md`
- `docs/backend/research/composite_rule_strategy.md`
- `docs/backend/research/research_caching.md`
- `docs/backend/research/parallel_research_execution.md`
- `docs/backend/research/research_checkpoint_resume.md`
- `docs/backend/research/advanced_validation_framework.md`
- `docs/backend/research/market_regime_classification.md`
- `docs/backend/research/regime_conditioned_analysis.md`
- `docs/backend/research/ml_assisted_research.md`
- `docs/backend/simulation/research_execution_paths.md`

Storage, lineage, ingestion, and safety:

- `docs/backend/storage-lineage/storage.md`
- `docs/backend/storage-lineage/universe.md`
- `docs/backend/storage-lineage/feature_dependency_resolution.md`
- `docs/backend/storage-lineage/indicator_vs_feature_architecture.md`
- `docs/backend/ingestion/ingestion.md`
- `docs/backend/safety/safety.md`

Observability and orchestration:

- `docs/backend/observability/instrumentation_inventory.md`
- `docs/backend/observability/correlation_conventions.md`
- `docs/backend/observability/alerting.md`
- `docs/backend/orchestration/trading-cycle.md`
- `docs/backend/orchestration/ingestion-cycle.md`
- `docs/backend/orchestration/scheduler.md`
- `docs/backend/runtime/failure-modes.md`

## Files Identified as Historical/Audit

Documentation audit:

- `docs/audits/documentation_inventory_audit.md`

Agent findings and remediation plans:

- `docs/audits/agent-findings/feature_dependency_integration_audit.md`
- `docs/audits/agent-findings/portfolio_governance_allocation_audit.md`
- `docs/audits/agent-findings/research_orchestration_observability_audit.md`
- `docs/audits/agent-findings/research_strategy_audit.md`

Execution/simulation audit:

- `docs/audits/execution_simulation_audit.md`

Frontend history:

- `docs/frontend/claude_frontend_previous_story.md`
- `docs/frontend/vite_readme.md`

Templates:

- `docs/templates/sor_template.md`

## Placeholder/Stubs Created

- `docs/README.md`
- `docs/backend/cli/cli.md`
- `docs/operations/runbooks/README.md`
- `docs/operations/debugging/README.md`
- `docs/implementation-summaries/documentation_tree_migration_summary.md`

## Follow-Up Recommendations

- Add README indexes under high-traffic folders such as `docs/backend/research/`, `docs/backend/execution/`, `docs/backend/observability/`, and `docs/audits/`.
- Reconcile stale root references in `CLAUDE.md` after deciding whether that file should remain an agent-only context file.
- Review audit findings against current code before merging them into canonical docs.
- Expand thin placeholders such as `docs/backend/research/research.md` and `docs/backend/simulation/backtesting.md`.
- Decide whether root `CHANGELOG.md` should remain root-level or be mirrored from `docs/implementation-summaries/`.
- Review moved frontend docs and replace generic Vite content with a project-specific frontend index later.
