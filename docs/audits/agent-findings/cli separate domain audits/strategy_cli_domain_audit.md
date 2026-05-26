# Strategy CLI Domain Audit

Target CLI domain: `strategy`
Target CLI file: `src/autonomous_trading_platform/cli/commands/strategy.py`

## 1. Current CLI Inventory

| Command Path | Arguments / Options | Handler | Mutates State? | Calls External APIs? | Safe For Local Read-Only Testing? | Phase Classification |
|---|---|---|---:|---:|---:|---|
| `strategy evaluate-bar` | `--timestamp` required | `handle_evaluate_bar(args)` | yes | yes | no | `BROKER_OR_EXTERNAL` |
| `strategy inspect-readiness` | `--timestamp` optional | `handle_inspect_readiness(args)` | no | conditional | yes | `READ_ONLY_SAFE` |

Notes:
- `strategy evaluate-bar` calls `run_trading_evaluation_cycle(timestamp=...)`, which builds full trading-cycle dependencies, evaluates the stub strategy, writes signals/checkpoints/run manifests, updates strategy runtime state, syncs broker equity, reads broker positions, reads latest broker trades, and generates order intents.
- `strategy inspect-readiness` calls `check_ingestion_readiness_job(...)`, which computes whether the current 5-minute cycle is past the ingestion deadline. It records metrics/traces but does not persist DB state.
- External API exposure for `evaluate-bar` is direct through broker account/position/trade reads. External exposure for `inspect-readiness` is only conditional observability export depending on telemetry setup/process configuration.

## 2. Domain Responsibility Check

| Command | Placement | Assessment |
|---|---|---|
| `strategy evaluate-bar` | should move to another domain or be wrapped elsewhere | The name suggests strategy-only evaluation, but the implementation is a trading evaluation cycle with broker reads, portfolio construction, runtime state updates, and order-intent generation. The canonical owner should be `runtime` for cycle orchestration or `execution` if order-intent side effects are the focus. A strategy-domain wrapper could remain only if renamed and guarded. |
| `strategy inspect-readiness` | should move to another domain | This inspects ingestion/runtime timing readiness, not strategy catalog/evaluation readiness. It fits `operations` for operational verification or `diagnostics` for read-only state inspection. |

Domain expectation:
- `strategy` should own strategy definitions, registry metadata, parameter schemas, strategy configuration inspection, strategy-level evaluation/debugging, and strategy catalog inspection.
- `controls` should own enabling/disabling strategy toggles.
- `governance` should own promotion/demotion transitions.
- `portfolio` or `risk` should own allocation changes and allocation constraint inspection.
- `runtime` should own scheduler/cycle execution.

## 3. Missing CLI Coverage

| Proposed Command Path | Purpose | Why It Belongs In This Domain | Type | Implementation Target | Priority |
|---|---|---|---|---|---|
| `strategy list-types` | List registered strategy types, families, production/debug flags, warmup bars, indicators, and persisted feature requirements. | Strategy registry metadata is core strategy-domain surface. | read-only | `strategy.registry.get_registry()`, currently duplicated in `research list-strategy-types` | P0 |
| `strategy inspect-type --strategy-type momentum` | Inspect one strategy definition, default parameters, schema, compatibility, warmup, indicators, features. | Operators need strategy metadata without entering research workflows. | read-only | `get_registry().get_definition(...)`, existing `research inspect-strategy` helper logic | P0 |
| `strategy validate-config --strategy-type momentum --parameters '{"lookback":20}'` | Validate and normalize parameters for a strategy type. | Makes strategy configs testable before research, governance, or live/paper enablement. | read-only | `StrategyRegistry.validate_parameters(...)`, `normalize_parameters(...)` | P0 |
| `strategy list` | List persisted strategies with status and key metrics. | REST already exposes strategy catalog state; CLI parity is needed for local operation. | read-only | `StrategyCatalogService.list_strategies(...)` | P1 |
| `strategy inspect --strategy-id strat_momentum_v1` | Show persisted strategy config, metrics, governance status, control status, deployment history. | Strategy-domain operator introspection. | read-only | `StrategyCatalogService.get_strategy_detail(...)` | P1 |
| `strategy compare --strategy-ids strat_a,strat_b` | Compare metrics for selected persisted strategies. | Strategy selection and review belong here; research can still own experiment generation. | read-only | `StrategyCatalogService.compare_strategies(...)` | P1 |
| `strategy equity-curve --strategy-id strat_momentum_v1` | Read latest equity curve for one strategy. | Useful to evaluate deployed or candidate strategy performance from CLI. | read-only | `StrategyCatalogService.get_strategy_equity_curve(...)` | P2 |
| `strategy evaluate-dry-run --strategy-type momentum --symbols AAPL,MSFT --timestamp 2026-05-26T15:35:00Z --dataset-version-id raw_bars_v1` | Evaluate strategy logic against local bars without broker reads, order intent generation, or DB writes. | This is the strategy-safe counterpart to the current broad `evaluate-bar`. | read-only | `StrategyEvaluationService` with local context builder and null writers | P1 |
| `strategy explain-signal --strategy-type momentum --symbol AAPL --timestamp 2026-05-26T15:35:00Z` | Emit signal plus explainability/context for a single symbol. | Strategy debugging should be possible without runtime or broker effects. | read-only | `StrategyFactory`, `StrategyContextBuilder`, strategy `evaluate_symbol(...)` | P2 |
| `strategy list-components` | List registered strategy components/indicators/rules. | Components are strategy construction primitives; research can wrap this, but strategy should own it. | read-only | `get_component_registry()`, currently in `research list-components` | P2 |
| `strategy inspect-component --component-name simple_moving_average` | Inspect component metadata, inputs, parameters, compatibility, warmup. | Helps debug strategy composition and parameter validation. | read-only | `get_component_registry().get_component_definition(...)` | P2 |
| `strategy active` | List active paper/live strategies. | Strategy-domain read-only view; controls/governance still own mutations. | read-only | `ActiveStrategiesService.list_active_strategies(...)` | P2 |

## 4. Testing Plan

### Phase 0: Help Commands

```powershell
python -m autonomous_trading_platform.cli.main --help
python -m autonomous_trading_platform.cli.main strategy --help
python -m autonomous_trading_platform.cli.main strategy evaluate-bar --help
python -m autonomous_trading_platform.cli.main strategy inspect-readiness --help
```

### Phase 1: Safe Read-Only Commands

Current safe command:

```powershell
python -m autonomous_trading_platform.cli.main strategy inspect-readiness --timestamp 2026-05-26T15:35:00Z
```

Recommended once added:

```powershell
python -m autonomous_trading_platform.cli.main strategy list-types --format json
python -m autonomous_trading_platform.cli.main strategy inspect-type --strategy-type momentum --format json
python -m autonomous_trading_platform.cli.main strategy validate-config --strategy-type momentum --parameters '{"lookback":20,"buy_above":0.0}'
python -m autonomous_trading_platform.cli.main strategy list --status paper --format json
python -m autonomous_trading_platform.cli.main strategy inspect --strategy-id strat_momentum_v1 --format json
python -m autonomous_trading_platform.cli.main strategy compare --strategy-ids strat_momentum_v1,strat_mean_reversion_v1 --format json
```

### Phase 2: Local DB Mutation Commands

There are no strategy-domain commands that should be considered safe local-only mutations today. `strategy evaluate-bar` mutates local DB state and also reads broker APIs, so it belongs in Phase 4.

Potential future local-only command examples:

```powershell
python -m autonomous_trading_platform.cli.main strategy import-config --strategy-id strat_momentum_v1 --strategy-type momentum --parameters-file artifacts/strategy/momentum.json --dry-run
python -m autonomous_trading_platform.cli.main strategy import-config --strategy-id strat_momentum_v1 --strategy-type momentum --parameters-file artifacts/strategy/momentum.json --actor local-operator --reason "seed local strategy catalog"
```

### Phase 3: Cross-Domain / Runtime Commands

The current `evaluate-bar` is effectively runtime/execution/broker-facing, not merely cross-domain. If retained as a runtime wrapper, test only in a configured paper environment:

```powershell
python -m autonomous_trading_platform.cli.main runtime trigger-job --job-name trading_evaluation_job --timestamp 2026-05-26T15:35:00Z --dry-run
```

### Phase 4: Broker / External Commands

Current broker-facing command:

```powershell
python -m autonomous_trading_platform.cli.main strategy evaluate-bar --timestamp 2026-05-26T15:35:00Z
```

Run only with paper credentials, a disposable local DB, and an explicit expectation that broker account, positions, and latest trades may be read.

## 5. Risks / Suspicious Wiring

- `strategy evaluate-bar` has a misleading name. It sounds like a strategy-only one-bar evaluation, but it calls the full trading evaluation cycle and reads broker account/positions/trades.
- `strategy evaluate-bar` has no `--dry-run`, despite writing signals, checkpoints, run manifests, strategy runtime state, and generating order intents.
- `strategy evaluate-bar` has no explicit safety gate or `--paper-only` guard at the CLI layer, even though it constructs execution and safety contexts and talks to broker infrastructure.
- `strategy evaluate-bar` does not expose strategy selection. The underlying trading-cycle dependency builder hard-wires `StubStrategy()` and manifest `strategy_id="baseline_strategy"`, so the command is not useful for evaluating an arbitrary strategy from the strategy catalog.
- `strategy evaluate-bar` prints only timestamp and `status: success`; it discards returned signal count, target bar timestamp, run ID, and generated order-intent summary.
- `strategy evaluate-bar` can raise if no target bar is ready because `run_trading_evaluation_job(...)` requires `target_bar_timestamp` for order-intent generation, even after `EvaluateStrategyJob` returns an unevaluated result.
- `strategy inspect-readiness` is domain-misaligned. It checks ingestion deadline readiness, not strategy readiness. It should move to `operations`, `diagnostics`, or `ingestion`.
- `strategy inspect-readiness` names the output "Inspect Readiness" and returns `ready/safe_mode/reason`, but does not identify that the readiness is ingestion-deadline readiness.
- `StrategyDependencies` is an empty dataclass and currently looks like a placeholder.
- There are no strategy CLI commands for the strong registry/catalog capabilities already present in the repo.
- Strategy registry inspection already exists under `research`; this creates discoverability drift because metadata about strategy types is not available under `strategy`.
- No focused CLI tests were found for `strategy evaluate-bar` or `strategy inspect-readiness`.

## 6. Recommended Refactor / Extension

- Move or wrap `strategy evaluate-bar` under `runtime` as a trading evaluation cycle command, or rename it to make broker/runtime effects explicit.
- Add a true strategy-only read-only evaluator, such as `strategy evaluate-dry-run`, that does not build execution context, call broker APIs, write signals, or generate order intents.
- Move `strategy inspect-readiness` to `operations inspect-ingestion-readiness` or `diagnostics ingestion-readiness`; if retained temporarily, rename output to clarify ingestion readiness.
- Add strategy registry commands: `list-types`, `inspect-type`, and `validate-config`.
- Add strategy catalog read commands: `list`, `inspect`, `compare`, `equity-curve`, and `active`.
- Add `--dry-run`, `--json`, and richer artifact output for any command that evaluates strategies.
- Add a CLI safety gate for any command that touches broker-backed execution context.
- Add focused CLI tests for parser registration, JSON output, timestamp parsing, broker-facing command guard behavior, and registry command parity with `research`.

## 7. Final Summary Table

| Command | Current Status | Correct Domain? | Risk | Next Action |
|---|---|---:|---|---|
| `strategy evaluate-bar` | Functional but broad runtime/execution path hidden behind strategy name | no | High | Move/wrap under `runtime`, add dry-run/safety gate/output IDs, add true strategy-only evaluator |
| `strategy inspect-readiness` | Read-only ingestion deadline check | no | Low | Move to `operations` or `diagnostics`, rename output to ingestion readiness |
