# Research Checkpoint And Resume

Research checkpointing is scoped to long-running research experiments and
simulation pipelines. It does not apply to live trading, paper trading,
broker submission, scheduler retries, reconciliation, or runtime control loops.

## Resumable Units

Already resumable through deterministic cache or artifact identity:

- Strategy generation configs use `StrategyConfig.config_hash()` and
  `StrategyGenerationCacheKey`.
- Individual simulation requests use `SimulationCacheKey` plus
  `SimulationArtifactIdentity`.
- Pipeline simulation stages use stable `stage_name` and `window_role`.
- Walk-forward train/test windows use `fold_N_train` and `fold_N_test`.
- Monte Carlo trials use `mc_run_N` and deterministic seed derivation.
- Validation, regime analysis, and intelligence artifacts already persist
  deterministic outputs, but their execution paths are not fully wrapped by
  checkpoint execution yet.

Partially resumable:

- Pipeline stages can skip completed/cache-hit simulation units when a
  `ResearchCheckpointService` is injected.
- Restart plans can include validation, regime, and intelligence identities,
  but execution integration for those services should remain separate and
  research-only.

Deferred:

- Distributed execution, background continuation scheduling, and external
  queues are intentionally out of scope.
- Bar-by-bar simulation internals are not checkpointed.

## Restart Semantics

For each expected research unit, resume checks run in this order:

1. Exact cache hit exists: skip execution and mark `cache_hit`.
2. Completed checkpoint exists: skip execution and keep `completed`.
3. Failed or missing checkpoint exists: rerun when the selected resume mode
   allows it.
4. Identity mismatch: fail the plan with an unsafe-to-resume reason.

`RestartPlan` reports completed, missing, failed, cache-hit, rerun, skipped,
and unsafe units. Dry-run mode builds the same plan without executing work.

## CLI

Research-only tooling lives under the `research` domain:

```bash
atp research inspect-checkpoints --checkpoint-store artifacts/research/checkpoints.json
atp research plan-restart --checkpoint-store artifacts/research/checkpoints.json --units-file artifacts/research/units.json
atp research resume-experiment --checkpoint-store artifacts/research/checkpoints.json --units-file artifacts/research/units.json --dry-run
```

`resume-experiment` is intentionally a planner entry point. Actual execution
uses `ResearchCheckpointService` inside research pipeline stages, not global
runtime orchestration.

## Parallel Execution Notes

Research checkpoints are updated per simulation unit and the checkpoint service
serializes in-memory status changes and JSON persistence with a lock. In
parallel research mode, a unit that fails is marked failed before the executor
returns the structured failure summary. Completed and cache-hit units remain
available to restart planning, so reruns can continue from completed work.

Parallel execution does not share SQLAlchemy sessions safely by itself. Stages
should only enable parallel mode when the underlying runner and writers are
isolated or otherwise safe for local threaded execution.
