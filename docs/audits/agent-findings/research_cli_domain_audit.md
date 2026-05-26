# Research CLI Domain Audit

Target CLI domain: `research`
Target CLI file: `src/autonomous_trading_platform/cli/commands/research.py`

## 1. Current CLI Inventory

| Command Path | Arguments / Options | Handler | Mutates State? | Calls External APIs? | Safe for Local Read-Only Testing? | Phase |
|---|---|---|---:|---:|---:|---|
| `research run-simulation` | `--dataset-version-id`, `--price-basis raw\|adjusted`, `--symbols`, `--start-date`, `--end-date`, `--strategy-type`, `--strategy-parameters`, `--random-seed`, `--shuffle-timestamps`, `--strategy-id`, `--initial-cash`, `--experiment-id`, `--universe-version`, `--strict-data-loading` | `handle_run_simulation` | Conditional | No | No | `LOCAL_DB_MUTATION` |
| `research run-experiment` | `--config`, `--experiment-id`, `--dataset-version-id`, `--price-basis raw\|adjusted`, `--symbols`, `--start-date`, `--end-date`, `--strategy-type`, `--strategy-parameters`, `--random-seed`, `--parameter-grid`, `--parameter-space`, `--universe-version`, `--initial-cash`, `--execution-mode`, `--max-workers`, `--base-seed`, `--fail-fast` | `handle_run_experiment` | Yes | No | No | `PLATFORM_WORKFLOW` |
| `research list-strategy-types` | `--family`, `--include-debug`, `--include-experimental`, `--format table\|json\|yaml` | `handle_list_strategy_types` | No | No | Yes | `READ_ONLY_SAFE` |
| `research inspect-strategy` | `--strategy-type`, `--format table\|json\|yaml` | `handle_inspect_strategy` | No | No | Yes | `READ_ONLY_SAFE` |
| `research list-components` | `--component-type`, `--executable-only`, `--metadata-only`, `--format table\|json\|yaml` | `handle_list_components` | No | No | Yes | `READ_ONLY_SAFE` |
| `research inspect-component` | `--component-name`, `--format table\|json\|yaml` | `handle_inspect_component` | No | No | Yes | `READ_ONLY_SAFE` |
| `research generate-strategies` | `--strategy-type`, `--family`, `--parameter-space`, `--parameter-space-file`, `--generator/--method grid\|random\|evolutionary`, `--n-samples`, `--random-seed/--seed`, `--show-configs`, `--population-size`, `--generations`, `--mutation-rate`, `--include-debug`, `--include-experimental`, `--allowed-families`, `--excluded-families`, `--composite`, `--summary`, `--verbose`, `--output`, `--output-format/--format json\|yaml`, `--include-run-metadata` | `handle_generate_strategies` | Conditional artifact write | No | Yes if no `--output` | `READ_ONLY_SAFE` |
| `research summarize-generated-configs` | `--input`, `--format json\|yaml\|table`, `--show-hashes` | `handle_summarize_generated_configs` | No | No | Yes | `READ_ONLY_SAFE` |
| `research inspect-checkpoints` | `--checkpoint-store`, `--format json\|yaml\|table` | `handle_inspect_checkpoints` | No | No | Yes | `READ_ONLY_SAFE` |
| `research plan-restart` | `--checkpoint-store`, `--units-file`, `--resume-failed-only`, `--resume-missing-only`, `--force-rerun`, `--format json\|yaml\|table` | `handle_plan_restart` | No | No | Yes | `READ_ONLY_SAFE` |
| `research resume-experiment` | `--checkpoint-store`, `--units-file`, `--dry-run`, `--resume-failed-only`, `--resume-missing-only`, `--force-rerun`, `--format json\|yaml\|table` | `handle_resume_experiment` | No | No | Yes | `SUSPICIOUS` |

Notes:

- `run-simulation` is conditional because the `--experiment-id` path persists through experiment orchestration. The direct simulation path is presented as non-DB-writing in comments, but it still executes research simulation code and may produce artifacts depending on runner behavior.
- `generate-strategies` is read-only until `--output` is supplied, at which point it writes a local artifact.
- `resume-experiment` is classified as suspicious because it delegates to `handle_plan_restart` and intentionally only plans. It does not resume execution.

## 2. Domain Responsibility Check

| Command | Placement |
|---|---|
| `research run-simulation` | Correctly placed. This is research simulation, not live execution. |
| `research run-experiment` | Correctly placed. This is the main experiment orchestration entrypoint. |
| `research list-strategy-types` | Should be duplicated or moved to `strategy`; research can keep a wrapper because generation depends on strategy catalogs. |
| `research inspect-strategy` | Should be duplicated or moved to `strategy`; research can keep a wrapper for experiment authoring. |
| `research list-components` | Should be duplicated or moved to `strategy`; research can keep a wrapper for generation/composition workflows. |
| `research inspect-component` | Should be duplicated or moved to `strategy`; research can keep a wrapper for generation/composition workflows. |
| `research generate-strategies` | Correctly placed. This is research candidate generation. |
| `research summarize-generated-configs` | Correctly placed. This supports generated research artifacts. |
| `research inspect-checkpoints` | Correctly placed. This is research pipeline/debug inspection. |
| `research plan-restart` | Correctly placed. This is research checkpoint recovery planning. |
| `research resume-experiment` | Should be renamed, deprecated, or implemented as a real resume command. Current behavior belongs under `plan-restart`, not `resume-experiment`. |

Overall, the domain belongs in `research`, but some catalog/introspection commands overlap with `strategy`. That overlap is acceptable as wrappers, but `strategy` should own the canonical strategy/component discovery surface.

## 3. Missing CLI Coverage

| Proposed Command Path | Purpose | Why It Belongs | Type | Target Service / Function | Priority |
|---|---|---|---|---|---|
| `research list-experiments` | List experiment records with status, created time, strategy count, and run count. | Research currently can create/run experiments but cannot inspect the catalog from CLI. | Read-only | `ExperimentCatalogService` / experiment repository | P0 |
| `research inspect-experiment --experiment-id EXP_ID` | Show experiment definition, status, runs, generated strategies, artifacts, and failures. | Required to operate and debug experiments created by `run-experiment`. | Read-only | `ExperimentCatalogService` | P0 |
| `research cancel-experiment --experiment-id EXP_ID --reason TEXT` | Cancel an experiment from CLI. | REST routes expose cancellation; CLI should support administrative research operation. | Local-mutating | `ExperimentCatalogService.cancel_experiment` or route-equivalent service | P0 |
| `research list-experiment-strategies --experiment-id EXP_ID` | List generated/tested strategies for an experiment. | Makes experiment output inspectable without direct DB queries. | Read-only | `ExperimentCatalogService.get_experiment_strategies` | P0 |
| `research validate-config --config experiment.yaml` | Validate experiment YAML, staged pipeline config, parameter spaces, strategy types, and required data settings without running. | High-value safety check before expensive simulation. | Read-only | `ExperimentConfig`, `StageRegistry`, staged pipeline config loader | P0 |
| `research plan-experiment --config experiment.yaml --format json` | Expand staged pipeline/parameter grid into planned work units without execution. | Gives operators a deterministic preview of run count and resource shape. | Read-only | Experiment orchestration planning code | P0 |
| `research list-runs --experiment-id EXP_ID` | List simulation runs for an experiment. | Current CLI runs experiments but lacks run-level inspection. | Read-only | Simulation/experiment repositories | P1 |
| `research inspect-run --run-id RUN_ID` | Inspect metrics, status, parameters, artifacts, failures, and lineage for one run. | Needed for practical debugging of research results. | Read-only | Simulation result repository / result recorder | P1 |
| `research export-experiment-bundle --experiment-id EXP_ID --output artifacts/research/EXP_ID` | Emit a complete reproducibility bundle: config, run summaries, metrics, generated strategies, checkpoints, and environment metadata. | Research workflows should produce portable evidence bundles. | Local artifact write | Experiment repositories, artifact repositories | P1 |
| `research resolve-feature-dependencies --strategy-type TYPE --symbols AAPL,MSFT --dataset-version-id ID --price-basis adjusted --start-date 2026-01-01 --end-date 2026-03-31` | Show feature dependencies required by a strategy over a simulation window. | The epic added simulation feature dependency resolution; CLI should expose it. | Read-only | `FeatureDependencyResolverService` | P1 |
| `research inspect-cache --cache strategy-generation\|simulation-results` | Show cache entries, hit/miss metadata, freshness, and identity keys. | Research cache services exist but are not operable from CLI. | Read-only | `strategy_generation_cache`, `simulation_result_cache`, cache validation services | P1 |
| `research validate-cache --cache strategy-generation\|simulation-results` | Validate cache identity/freshness and report invalid entries. | Prevents stale research artifacts from contaminating results. | Read-only | `cache_validation`, `cache_identity`, `cache_key_builder` | P1 |
| `research clear-cache --cache NAME --dry-run` | Remove selected research cache entries, guarded by dry-run. | Cache cleanup is operationally necessary but should be explicit and auditable. | Local-mutating | Research cache services | P2 |
| `research run-validation --type walk-forward --config validation.yaml` | Run walk-forward, stress, survivorship, overfitting, parameter-sensitivity, or robustness validation. | Validation services exist and are central to research quality. | Local-mutating / artifact write | `validation_orchestrator`, walk-forward/stress/survivorship/overfitting services | P1 |
| `research analyze-regimes --experiment-id EXP_ID --output artifacts/research/regimes.json` | Produce regime metrics, regime profiles, transitions, and joined run/regime analysis. | Regime analysis is research evaluation, not runtime operation. | Local artifact write | `regime_analysis_service`, `regime_join_service`, transition/profile services | P1 |
| `research intelligence rank-candidates --experiment-id EXP_ID --format json` | Rank generated strategies using research intelligence outputs. | Candidate ranking is part of research selection. | Read-only or artifact write | `ResearchIntelligenceService`, `CandidateRankingService` | P1 |
| `research intelligence cluster-strategies --experiment-id EXP_ID --output clusters.json` | Cluster strategy candidates and expose diversity/duplication. | Strategy clustering was added but lacks CLI exposure. | Local artifact write | `StrategyClusteringService` | P1 |
| `research intelligence predict-robustness --experiment-id EXP_ID` | Estimate robustness for candidate strategies. | Robustness prediction is research intelligence, not execution. | Read-only or artifact write | `RobustnessPredictionService` | P1 |
| `research calibrate-slippage --fills-source PATH --output calibration.json` | Build slippage/fill-quality calibration snapshots from fills. | Calibration belongs to research when used for simulation realism. | Local artifact write | `SlippageCalibrationService`, `FillQualityAggregator`, `CalibrationSnapshotStore` | P2 |
| `research inspect-cost-model --config simulation.yaml` | Preview simulated cost model assumptions. | Useful before running simulations that depend on costs/slippage. | Read-only | `SimulationCostModelService`, simulated execution services | P2 |

## 4. Testing Plan

### Phase 0: `--help` Commands

```powershell
python -m autonomous_trading_platform.cli.main research --help
python -m autonomous_trading_platform.cli.main research run-simulation --help
python -m autonomous_trading_platform.cli.main research run-experiment --help
python -m autonomous_trading_platform.cli.main research list-strategy-types --help
python -m autonomous_trading_platform.cli.main research inspect-strategy --help
python -m autonomous_trading_platform.cli.main research list-components --help
python -m autonomous_trading_platform.cli.main research inspect-component --help
python -m autonomous_trading_platform.cli.main research generate-strategies --help
python -m autonomous_trading_platform.cli.main research summarize-generated-configs --help
python -m autonomous_trading_platform.cli.main research inspect-checkpoints --help
python -m autonomous_trading_platform.cli.main research plan-restart --help
python -m autonomous_trading_platform.cli.main research resume-experiment --help
```

### Phase 1: Safe Read-Only Commands

```powershell
python -m autonomous_trading_platform.cli.main research list-strategy-types --format table
python -m autonomous_trading_platform.cli.main research list-strategy-types --family momentum --include-experimental --format json
python -m autonomous_trading_platform.cli.main research inspect-strategy --strategy-type momentum --format json
python -m autonomous_trading_platform.cli.main research list-components --format table
python -m autonomous_trading_platform.cli.main research list-components --component-type signal --executable-only --format json
python -m autonomous_trading_platform.cli.main research inspect-component --component-name moving_average_signal --format json
python -m autonomous_trading_platform.cli.main research generate-strategies --strategy-type momentum --generator grid --summary --format json
python -m autonomous_trading_platform.cli.main research summarize-generated-configs --input artifacts/research/generated_strategies.json --format table --show-hashes
python -m autonomous_trading_platform.cli.main research inspect-checkpoints --checkpoint-store artifacts/research/checkpoints --format json
python -m autonomous_trading_platform.cli.main research plan-restart --checkpoint-store artifacts/research/checkpoints --units-file artifacts/research/work_units.json --resume-failed-only --format json
python -m autonomous_trading_platform.cli.main research resume-experiment --checkpoint-store artifacts/research/checkpoints --units-file artifacts/research/work_units.json --resume-failed-only --format json
```

### Phase 2: Local DB Mutation / Artifact Commands

```powershell
python -m autonomous_trading_platform.cli.main research generate-strategies --strategy-type momentum --generator random --n-samples 25 --seed 42 --output artifacts/research/generated_strategies.json --output-format json --include-run-metadata
python -m autonomous_trading_platform.cli.main research run-simulation --dataset-version-id raw_bars_2026_05_01 --price-basis raw --symbols AAPL,MSFT --start-date 2026-01-01 --end-date 2026-03-31 --strategy-type momentum --strategy-parameters '{"lookback":20,"buy_above":0.0}' --random-seed 42 --strategy-id research_momentum_smoke --initial-cash 100000 --strict-data-loading
python -m autonomous_trading_platform.cli.main research run-simulation --dataset-version-id raw_bars_2026_05_01 --price-basis raw --symbols AAPL,MSFT --start-date 2026-01-01 --end-date 2026-03-31 --strategy-type momentum --strategy-parameters '{"lookback":20,"buy_above":0.0}' --random-seed 42 --strategy-id research_momentum_smoke --experiment-id exp_research_smoke_001 --initial-cash 100000 --strict-data-loading
python -m autonomous_trading_platform.cli.main research run-experiment --experiment-id exp_research_smoke_002 --dataset-version-id raw_bars_2026_05_01 --price-basis raw --symbols AAPL,MSFT --start-date 2026-01-01 --end-date 2026-03-31 --strategy-type momentum --strategy-parameters '{"lookback":20,"buy_above":0.0}' --random-seed 42 --initial-cash 100000 --execution-mode sequential --max-workers 1
python -m autonomous_trading_platform.cli.main research run-experiment --config configs/research/experiment_smoke.yaml --execution-mode sequential --max-workers 1 --base-seed 42
```

### Phase 3: Cross-Domain / Runtime Commands

Current research commands do not orchestrate the live runtime engine or scheduler. The closest cross-domain workflows are staged research pipelines inside `run-experiment`; test those with small configs and `--max-workers 1` before enabling parallel execution.

```powershell
python -m autonomous_trading_platform.cli.main research run-experiment --config configs/research/staged_pipeline_smoke.yaml --execution-mode sequential --max-workers 1 --base-seed 42 --fail-fast
```

### Phase 4: Broker / External Commands

No current `research` command should call broker/live trading APIs. If future commands use external data providers, require explicit provider options, offline fixtures for tests, and a `--dry-run` or `--offline` mode.

## 5. Risks / Suspicious Wiring

- `research resume-experiment` is misleading. It never resumes work; it calls `handle_plan_restart` and only emits a restart plan.
- `resume-experiment --dry-run` is effectively always true and is not used to change behavior.
- `run-simulation --experiment-id` and `run-experiment` mutate experiment/run state and artifacts but do not expose a `--dry-run` or plan-only mode.
- `run-experiment` has conditionally required inline arguments enforced in the handler, not by parser groups. Help output may understate what is required when `--config` is omitted.
- `run-simulation` direct mode is described as not persisting to DB, but the simulation context includes repositories and the runner may still write artifacts or records. Treat it as non-read-only.
- Strategy and component catalog commands are useful, but their canonical home should be `strategy`; keeping them only under `research` creates domain drift.
- There is no CLI coverage for experiment listing, detail, cancellation, or experiment strategies despite available application/API surfaces.
- There is no CLI coverage for the newer research validation, intelligence, cache, calibration, regime analysis, feature dependency, or cost-model services.
- Long-running experiment commands should emit machine-readable artifact bundles, not only stdout summaries.
- Mutating research commands should have explicit audit/event logging or at least durable run metadata with command arguments and environment fingerprints.

## 6. Recommended Refactor / Extension

- Keep `research` as the owner of experiments, simulation research, candidate generation, validation, regime analysis, and research intelligence.
- Add `research list-experiments`, `inspect-experiment`, `cancel-experiment`, and `list-experiment-strategies` as P0 operability commands.
- Add `validate-config` and `plan-experiment` before expanding more mutating experiment commands.
- Duplicate or move strategy/component catalog commands into `strategy`; keep lightweight wrappers in `research` only if they improve experiment authoring.
- Rename `resume-experiment` to `plan-restart`, deprecate it, or implement a real guarded resume flow with `--execute`.
- Add `--dry-run` or plan-only behavior to `run-experiment` and experiment-affecting commands.
- Add JSON/artifact output options for `run-simulation` and `run-experiment`.
- Add CLI coverage for validation, cache, regime analysis, calibration, and research intelligence services from the recent research epic.
- Add audit logging or durable run metadata for commands that create or mutate experiments, runs, caches, or calibration snapshots.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `research run-simulation` | Functional research simulation entrypoint | Yes | Medium | Add dry-run/plan and explicit artifact output; clarify persistence behavior. |
| `research run-experiment` | Main experiment/staged pipeline runner | Yes | Medium | Add `validate-config`, `plan-experiment`, artifact bundles, and stronger audit metadata. |
| `research list-strategy-types` | Read-only catalog inspection | Partial | Low | Duplicate or move canonical command to `strategy`; keep research wrapper if useful. |
| `research inspect-strategy` | Read-only strategy inspection | Partial | Low | Duplicate or move canonical command to `strategy`. |
| `research list-components` | Read-only component inspection | Partial | Low | Duplicate or move canonical command to `strategy`. |
| `research inspect-component` | Read-only component detail | Partial | Low | Duplicate or move canonical command to `strategy`. |
| `research generate-strategies` | Useful candidate generation command | Yes | Low | Keep; add stronger integration with experiment creation and cache inspection. |
| `research summarize-generated-configs` | Useful artifact inspection command | Yes | Low | Keep; optionally support experiment-linked summaries. |
| `research inspect-checkpoints` | Safe checkpoint inspection | Yes | Low | Keep; add checkpoint/run linkage in output. |
| `research plan-restart` | Safe restart planning | Yes | Low | Keep; make it the canonical recovery planning command. |
| `research resume-experiment` | Misleading alias for plan-only behavior | No | Medium | Deprecate/rename or implement guarded real resume with `--execute`. |
