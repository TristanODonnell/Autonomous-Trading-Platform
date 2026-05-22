# Strategy Generation Engine

`StrategyGenerationEngine` expands registered strategy metadata into deterministic
`StrategyConfig` candidates for research dry runs and experiment sweeps.

## Generation Methods

- `grid`: resolves a deterministic cartesian product from explicit overrides or
  `StrategyRegistry.parameter_specs`.
- `random`: samples resolved values with a local `random.Random(seed)`.
- `evolutionary`: builds an initial seed population and emits deterministic
  mutation-driven candidates. It does not consume backtest fitness yet.

The legacy list-returning API remains available through `generate(...)`.
Call `generate_result(...)`, `generate_for_family(...)`, or
`generate_composite(...)` when summary metadata is needed.

## Registry Metadata

`ParameterSpaceResolver` is the canonical parameter-space resolver. It:

- rejects unknown parameter names;
- derives default search values from `ParameterSpec` ranges and discrete flags;
- coerces values according to `ParameterType`;
- rejects values outside per-parameter min/max bounds;
- leaves cross-field rules, such as `short_window < long_window`, to the
  registered Pydantic schema during candidate normalization.

Every accepted candidate is normalized through `StrategyConfig`, which delegates
to `StrategyRegistry.normalize_parameters(...)`.

## Determinism

Grid output is sorted by parameter name and value-list order. Random and
evolutionary generation use only the configured seed and registry metadata, so
the same seed and metadata produce the same output order.

## De-Duplication

The engine de-duplicates candidates by normalized `config_hash()`. The richer
`GenerationResult.summary` reports:

- generated candidates;
- accepted configs;
- duplicate skips;
- rejected candidates;
- rejection reasons;
- strategy type and family distributions.
- rejected candidate details;
- duplicate candidate details, including duplicate hashes.

The CLI exposes aggregate counts by default and candidate-level details with
`--verbose`.

## Composite Rule Generation

`generate_composite(...)` currently emits practical `composite_rule` templates:

- trend filter + mean-reversion entry + volume confirmation;
- momentum entry + volatility filter + weighted-score aggregation;
- SMA crossover entry + volume confirmation.

Templates validate through `CompositeStrategyConfig`, reference registered
components, use deterministic indicator IDs, and can be built by
`StrategyFactory`.

## Compatibility Filtering

Generation options support:

- `include_debug`;
- `include_experimental`;
- allowed/excluded families;
- allowed/excluded strategy types;
- execution mode (`daily` or `intraday`);
- price basis (`raw` or `adjusted`).

Debug and non-production strategies are excluded by default.

## CLI Tooling

Generation and inspection commands live under `research`:

- `research list-strategy-types`
- `research inspect-strategy`
- `research list-components`
- `research inspect-component`
- `research generate-strategies`
- `research summarize-generated-configs`

`generate-strategies` can export JSON or YAML artifacts containing normalized
configs, config hashes, generation options, summary metadata, component usage,
and composite template usage. See `docs/cli/strategy_generation.md`.

## Current Limitations

The evolutionary generator is mutation-only and does not perform fitness-based
selection. Generation results are not persisted, distributed, or ranked by
simulation performance.
