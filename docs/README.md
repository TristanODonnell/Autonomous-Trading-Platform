# Documentation Index

This directory is organized by documentation purpose and backend domain.

## Canonical Docs

- Architecture: `docs/architecture/`
- Backend API and contracts: `docs/backend/api/`
- Broker and reconciliation: `docs/backend/broker/`
- CLI and runtime harnesses: `docs/backend/cli/`
- Execution: `docs/backend/execution/`
- Ingestion and orchestration: `docs/backend/ingestion/`, `docs/backend/orchestration/`
- Observability: `docs/backend/observability/`
- Research and simulation: `docs/backend/research/`, `docs/backend/simulation/`
- Runtime safety: `docs/backend/safety/`
- Storage, lineage, universe, and features: `docs/backend/storage-lineage/`
- Universe operations: `docs/backend/universe/`

## Historical and Planning Docs

- Audits and agent findings: `docs/audits/`
- Implementation summaries: `docs/implementation-summaries/`
- Roadmaps: `docs/roadmaps/`
- Unsorted docs needing placement review: `docs/unsorted/`

## Placement Rules

- Put durable reference docs in the owning backend, frontend, operations, or architecture folder.
- Put remediation plans, implementation reviews, and technical-debt findings under `docs/audits/`.
- Put completed implementation notes under `docs/implementation-summaries/`.
- Put unclear docs under `docs/unsorted/` instead of guessing.
- Keep root-level docs limited to project overview, setup, changelog, and tool context.
