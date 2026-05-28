# Strategy Generation CLI

Strategy generation tooling lives under `research` because generation is a
research dry-run workflow, not live strategy execution.

## Registry Inspection

```bash
atp research list-strategy-types
atp research list-strategy-types --family momentum --format json
atp research inspect-strategy --strategy-type momentum

atp research list-components
atp research list-components --component-type indicator --executable-only
atp research list-components --metadata-only --format json
atp research inspect-component --component-name momentum
```

Strategy commands read from `StrategyRegistry`. Component commands read from
`ComponentRegistry` and mark `is_executable` and `metadata_only` explicitly.

## Generate

```bash
atp research generate-strategies --strategy-type momentum --generator grid
atp research generate-strategies --strategy-type momentum --generator random --random-seed 7 --n-samples 10
atp research generate-strategies --strategy-type momentum --generator evolutionary --population-size 8 --generations 2
atp research generate-strategies --composite
```

Useful flags:

- `--parameter-space` or `--parameter-space-file`
- `--family`
- `--include-debug`
- `--include-experimental`
- `--allowed-families`
- `--excluded-families`
- `--summary`
- `--verbose`
- `--output`
- `--output-format json|yaml`

The command prints generated, accepted, duplicate, and rejected counts,
rejection reasons, strategy/family distributions, config hashes, component
usage, and composite template usage. `--verbose` adds bounded rejected and
duplicate candidate details.

## Export Artifact

```bash
atp research generate-strategies --composite --output artifacts/composite.json
atp research generate-strategies --strategy-type momentum --output artifacts/momentum.yaml --output-format yaml
```

Artifact shape:

- `artifact_type`: `strategy_generation_result`
- `artifact_version`
- `generation`: generator, target strategy/family, parameter space, options
- `summary`: `GenerationSummary.to_dict()`
- `config_hashes`
- `component_usage`
- `composite_template_usage`
- `configs`: generated configs with normalized parameters and `config_hash`
- optional `run_metadata.generated_at` when `--include-run-metadata` is used

`config_hash` is computed from the normalized `StrategyConfig` only; optional
run metadata does not affect it.

## Summarize Artifact

```bash
atp research summarize-generated-configs --input artifacts/composite.json
atp research summarize-generated-configs --input artifacts/composite.json --show-hashes
```

Summaries include total configs, strategy and family distributions, numeric
parameter ranges, component usage, composite template usage, duplicate/rejected
counts when present, and config hashes when requested.

## Determinism

Grid output is deterministic by sorted parameter keys and registry order.
Random and evolutionary output are deterministic for the same seed, parameter
space, generator settings, and registry metadata.
