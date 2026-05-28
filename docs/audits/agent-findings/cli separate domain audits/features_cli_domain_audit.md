# Features CLI Domain Audit

Target CLI domain: `features`
Target CLI file: `src/autonomous_trading_platform/cli/commands/features.py`

## 1. Current CLI Inventory

| Command Path | Arguments / Options | Handler | Mutates State? | Calls External APIs? | Safe For Local Read-Only Testing? | Phase Classification |
|---|---|---|---:|---:|---:|---|
| `features run-pipeline` | `--dataset-version-id` required; `--price-basis {RAW,ADJUSTED}` default `RAW`; `--symbols`; `--start-date`; `--end-date`; `--include-returns`; `--include-volatility`; `--include-moving-average`; `--include-liquidity`; `--include-regime`; `--include-regime-classification` | `run_features(args)` | yes | conditional | no | `LOCAL_DB_MUTATION` |

Notes:
- The command invokes `run_feature_pipeline_cycle(...)`.
- It writes feature parquet outputs, feature dataset version rows, run manifests, runtime job run rows, metrics, tracing, and audit/activity events.
- It does not call a broker or market data provider directly.
- `setup_telemetry(...)` may attempt OTLP export to configured endpoints, so external/localhost observability calls are conditional.

## 2. Domain Responsibility Check

| Command | Placement | Assessment |
|---|---|---|
| `features run-pipeline` | correctly placed | This is the domain command for persisted feature generation from source market datasets. It belongs in `features`, with runtime/platform wrappers allowed elsewhere for orchestrated workflows. |

Recommended domain boundaries:
- Keep the standalone feature generation command in `features`.
- Runtime orchestration should wrap or call the feature cycle from `runtime`, not own feature-specific options.
- End-to-end product flows that include ingestion, features, research, and dashboard validation belong in `platform`.
- Feature dataset inspection could live in `features`; broad system state snapshots can be duplicated in `diagnostics`.

## 3. Missing CLI Coverage

| Proposed Command Path | Purpose | Why It Belongs In This Domain | Type | Implementation Target | Priority |
|---|---|---|---|---|---|
| `features list-datasets` | List feature dataset versions with filters such as `--feature-name`, `--source-dataset-version`, `--validated-only`, and `--limit`. | Operators need to discover generated feature artifacts before research/simulation use. | read-only | `FeatureDatasetVersionsRepository`, `FeatureDatasetAuditService.list_feature_datasets_for_source_dataset(...)` | P0 |
| `features inspect-dataset --feature-dataset-version-id feat_returns_...` | Show lineage, validation status, storage path, coverage, source dataset, and computation parameters. | Feature artifacts are domain objects; inspection should not require SQL/API calls. | read-only | `FeatureDatasetAuditService.inspect_feature_dataset(...)` | P0 |
| `features latest --feature-name returns --price-basis RAW` | Resolve the latest validated feature dataset for a feature and price basis. | This is the main preflight check before simulations and strategy evaluation consume features. | read-only | `FeatureDatasetVersionsRepository.get_latest_validated(...)` or `FeatureDatasetRegistrationService.get_latest_validated_dataset(...)` | P0 |
| `features validate-dataset --feature-dataset-version-id feat_returns_...` | Re-run metadata/contract validation and optionally parquet presence checks. | Feature datasets need independent operability after generation. | read-only by default; local-mutating only if `--mark-valid/--mark-failed` is added | `FeatureDatasetValidationService`, `FeatureDatasetAuditService`, parquet repository checks | P1 |
| `features plan-pipeline --dataset-version-id raw_bars_2026_05_01 --symbols AAPL,MSFT --start-date 2026-01-01 --end-date 2026-05-01` | Show which feature jobs would run or be reused by the guard service without writing outputs. | The current pipeline has recomputation guards but no CLI dry-run visibility. | read-only | `FeaturePipelineGuardService`, `FeatureDatasetResolverService`, job computation parameter builders | P1 |
| `features run-pipeline --dry-run ...` | Validate inputs, lineage, source coverage, and planned outputs without writing parquet or DB rows. | The mutating command needs a safe preflight path. | read-only | New dry-run branch around `run_feature_pipeline_cycle` or a planning service extracted from it | P1 |
| `features register-dataset-version ...` | Register an externally produced feature dataset version. | REST already supports feature dataset metadata creation; CLI parity would help local/admin workflows. | local-mutating | `FeatureDatasetCommandService.create_feature_dataset_version(...)` | P2 |
| `features sample-dataset --feature-dataset-version-id feat_returns_... --symbol AAPL --limit 20` | Read a small parquet sample for sanity checks. | Feature operators need to inspect actual computed values, not just metadata. | read-only | `ParquetFeatureRepository` | P2 |
| `features export-lineage --feature-dataset-version-id feat_returns_... --output artifacts/features/lineage.json` | Emit a JSON lineage artifact for audit packets or research reproducibility. | Feature dataset lineage is central to storage/research reproducibility. | read-only artifact output | `FeatureDatasetAuditService.inspect_feature_dataset(...)` plus JSON writer | P2 |
| `features resolve-for-simulation --strategy-type momentum --source-dataset-version raw_bars_v1 --start-date 2026-01-01 --end-date 2026-05-01 --price-basis RAW --symbols AAPL,MSFT` | Show feature dependencies that a research simulation would resolve. | Feature dependency resolution bridges features and research; a read-only CLI makes failures diagnosable. | cross-domain read-only | `FeatureDatasetVersionsRepository.find_for_simulation(...)`, research `FeatureDependencyResolverService` | P3 |

## 4. Testing Plan

### Phase 0: Help Commands

```powershell
python -m autonomous_trading_platform.cli.main --help
python -m autonomous_trading_platform.cli.main features --help
python -m autonomous_trading_platform.cli.main features run-pipeline --help
```

### Phase 1: Safe Read-Only Commands

No safe read-only feature commands are currently registered.

Recommended once added:

```powershell
python -m autonomous_trading_platform.cli.main features list-datasets --source-dataset-version raw_bars_2026_05_01 --validated-only --limit 20
python -m autonomous_trading_platform.cli.main features inspect-dataset --feature-dataset-version-id feat_returns_20260501_abcd1234
python -m autonomous_trading_platform.cli.main features latest --feature-name returns --price-basis RAW
python -m autonomous_trading_platform.cli.main features plan-pipeline --dataset-version-id raw_bars_2026_05_01 --symbols AAPL,MSFT,SPY --start-date 2026-01-01 --end-date 2026-05-01
```

### Phase 2: Local DB Mutation Commands

Run only against a disposable local database and data root:

```powershell
python -m autonomous_trading_platform.cli.main features run-pipeline --dataset-version-id raw_bars_2026_05_01 --price-basis RAW --symbols AAPL,MSFT,SPY --start-date 2026-01-01 --end-date 2026-05-01
```

Narrow feature run example, after parser fixes allow disabling default jobs:

```powershell
python -m autonomous_trading_platform.cli.main features run-pipeline --dataset-version-id raw_bars_2026_05_01 --price-basis RAW --symbols AAPL --start-date 2026-01-01 --end-date 2026-05-01 --include-returns --no-include-volatility --no-include-moving-average --no-include-liquidity --no-include-regime --no-include-regime-classification
```

### Phase 3: Cross-Domain / Runtime Commands

Current command writes runtime job runs and manifests but is not itself a runtime orchestration command.

Suggested wrapper validation once runtime/platform flows call it:

```powershell
python -m autonomous_trading_platform.cli.main runtime trigger-job --job-name feature_pipeline_cycle --dry-run
python -m autonomous_trading_platform.cli.main platform run-golden-path --symbols AAPL,MSFT --start-date 2026-01-01 --end-date 2026-05-01 --include-features
```

### Phase 4: Broker / External Commands

Not applicable. The feature domain should not call broker/live trading systems.

## 5. Risks / Suspicious Wiring

- The `--include-*` options are suspicious: each uses `action="store_true"` with `default=True`, so passing or omitting the option both result in `True`. The CLI cannot disable returns, volatility, moving averages, liquidity, regime, or regime classification, even though `run_feature_pipeline_cycle(...)` supports `False`.
- `--start-date`, `--end-date`, and `--symbols` are optional in the parser, but `FeatureDatasetResolverService.load_bars_frame(...)` requires symbols and both dates. If no active universe supplies symbols, or dates are omitted, the command fails after it has already started runtime/manifest bookkeeping.
- `--dataset-version-id` is required in the CLI even though `run_feature_pipeline_cycle(...)` supports `dataset_version_id=None` and has a latest-validated source path. Either the CLI should expose latest behavior intentionally or the cycle should remain explicit-only.
- `run_feature_pipeline_cycle(...)` accepts `universe_version_id`, but the CLI does not expose `--universe-version-id`.
- The command has no `--dry-run`, despite writing parquet feature datasets, feature dataset rows, run manifests, runtime job runs, and audit events.
- The command has no `--json` or `--output` option. Operators cannot directly capture `run_id`, `job_run_id`, generated feature dataset IDs, reused dataset IDs, or artifact paths from CLI output.
- The command offers `--price-basis ADJUSTED`, but the resolver's `load_bars_frame(...)` reads `RAW_BARS_DATASET` unconditionally. That may be correct only if adjusted bars are stored behind the same parquet dataset abstraction; otherwise adjusted feature generation can read the wrong physical dataset.
- The cycle records audit logging and runtime job runs, which is good. However, the CLI itself does not summarize those audit identifiers to stdout.
- There is no CLI test coverage for `features run-pipeline`; the scheduler cycle has direct tests, but parser/handler behavior is not covered.
- The feature pipeline command is operationally powerful but has no explicit local-environment guard, confirmation, or artifact manifest output.

## 6. Recommended Refactor / Extension

- Keep `features run-pipeline` in the `features` domain.
- Add read-only commands first: `list-datasets`, `inspect-dataset`, `latest`, and `plan-pipeline`.
- Fix include/exclude flags, preferably with `BooleanOptionalAction` or explicit `--skip-*` flags.
- Add `--dry-run` before adding more mutating feature pipeline options.
- Add `--json` and `--output` so generated dataset IDs, run IDs, job run IDs, and storage paths are machine-readable.
- Add parser support for `--universe-version-id`, or document that `--symbols` is the only supported CLI selector.
- Either make `--symbols`, `--start-date`, and `--end-date` required for `run-pipeline`, or add a preflight resolver that explains the active-universe/latest-window behavior before mutating state.
- Add focused CLI tests for parser defaults, include/disable behavior, missing required effective inputs, and successful handler invocation with a seeded local fixture.
- No split domain is needed. Runtime/platform should wrap this command or cycle for larger workflows rather than absorbing feature-specific controls.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `features run-pipeline` | Functional backend cycle exposed through a very thin CLI; mutates DB/parquet/runtime audit state | yes | Medium | Keep command, fix include flags, add dry-run/read-only inspection, add JSON/artifact output, add CLI tests |
