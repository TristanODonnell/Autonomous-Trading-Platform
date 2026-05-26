# Parallel Research Execution

TASK-3.2 adds a research-only local execution layer for independent work units.
It is intentionally not a distributed scheduler and does not use Celery, RQ,
Redis, or cloud workers.

## Parallelizable Boundaries

Safe local work units are:

- independent `SimulationStage` strategy simulations;
- independent train simulations within one walk-forward fold;
- independent test simulations within one walk-forward fold after train filtering;
- independent Monte Carlo trials for a single strategy;
- validation, regime, and intelligence jobs only when they have isolated inputs and
  writers.

Unsafe areas remain outside the parallel boundary:

- live and paper trading paths;
- shared SQLAlchemy sessions and repository objects;
- shared mutable writers that target the same artifact path;
- code that relies on process-global `random` or NumPy RNG state without a
  per-unit seed;
- order-dependent aggregations.

## Execution Model

`autonomous_trading_platform.research.execution` provides `ExecutionUnit`,
`ExecutionResult`, `ParallelExecutionService`, and `DeterministicSeedService`.
Serial mode is the default. Parallel mode is opt-in per stage with
`execution_mode: parallel` and `max_workers`.

Thread execution was chosen first because the current simulation runner owns
repository/session objects that are not safe to pickle for process execution.
Distributed execution is deferred until workers can construct isolated runner
instances and isolated repository sessions.

## Determinism Contract

Units are submitted in deterministic sort-key order and final results are sorted
by the same keys before aggregation. Serial and parallel execution must produce
the same logical result ordering.

Simulation and walk-forward seeds are derived from stable identity inputs:
base seed, experiment id, strategy id, strategy config hash, stage name, window
role, and fold id when present.

Monte Carlo retains the existing `base_seed + trial_index` seed contract for
compatibility, while trial identities remain isolated through `mc_run_N` window
roles.

## Cache, Checkpoint, And Artifact Safety

Cache lookups and writes happen per simulation request. `SimulationResultCache`
uses a lock and records duplicate keys idempotently.

Checkpoint status updates are also locked. A failed unit records its failed
checkpoint before the executor returns a structured failure summary. Completed
and cache-hit units are preserved for restart planning.

Artifact identity remains based on `SimulationArtifactIdentity`, including
stage name, window role, strategy id, seed, dates, dataset version, universe
version, and price basis. Distinct unit identities prevent path collisions.

Repository writes are not made thread-safe by this feature. Parallel execution
should be enabled only for runners whose writer/session dependencies are
isolated or known to be safe for the selected workload.

## Failure Behavior

The executor supports collect-errors mode by default and fail-fast mode when
requested. Collect-errors waits for all submitted units and raises
`ParallelExecutionError` with all failures sorted by unit order. Fail-fast raises
after the first completed failure and cancels pending work where possible.

Successful units remain completed. Resume can use checkpoint state to skip
completed or cache-hit units and retry failed or missing units.
