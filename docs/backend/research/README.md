# Research CLI

Status: Current as of CLI drift audit.

## Commands

| Command | Purpose | Source | Notes |
|---|---|---|---|
| `research run-simulation` | Run direct strategy simulation. | `src/autonomous_trading_platform/cli/commands/research.py` | Requires dataset, price basis, symbols, dates, strategy type/id, and seed. |
| `research run-experiment` | Run experiment orchestration. | `src/autonomous_trading_platform/cli/commands/research.py` | Supports config files or inline experiment options. |
| `research list-strategy-types` | Inspect registered strategy types. | `src/autonomous_trading_platform/cli/commands/research.py` | Read-only. |
| `research inspect-strategy` | Inspect one strategy definition. | `src/autonomous_trading_platform/cli/commands/research.py` | Requires `--strategy-type`. |
| `research list-components` | Inspect registered strategy components. | `src/autonomous_trading_platform/cli/commands/research.py` | Read-only. |
| `research inspect-component` | Inspect one component definition. | `src/autonomous_trading_platform/cli/commands/research.py` | Requires `--component-name`. |
| `research generate-strategies` | Generate strategy configs. | `src/autonomous_trading_platform/cli/commands/research.py` | See `docs/backend/cli/strategy_generation.md`. |
| `research summarize-generated-configs` | Summarize generated config artifact. | `src/autonomous_trading_platform/cli/commands/research.py` | Requires `--input`. |
| `research inspect-checkpoints` | Inspect checkpoint store. | `src/autonomous_trading_platform/cli/commands/research.py` | Requires `--checkpoint-store`. |
| `research plan-restart` | Plan checkpoint-based restart. | `src/autonomous_trading_platform/cli/commands/research.py` | Requires checkpoint store and units file. |
| `research resume-experiment` | Resume experiment units from checkpoints. | `src/autonomous_trading_platform/cli/commands/research.py` | Defaults to dry-run in parser. |
| `strategy evaluate-bar` | Evaluate strategy for a timestamp. | `src/autonomous_trading_platform/cli/commands/strategy.py` | Mutating/operational evaluation path. |
| `strategy inspect-readiness` | Inspect strategy readiness. | `src/autonomous_trading_platform/cli/commands/strategy.py` | Read-oriented. |

## Related Docs

- `docs/backend/cli/strategy_generation.md`
- `docs/backend/research/strategy_registry.md`
- `docs/backend/research/strategy_generation_engine.md`
- `docs/backend/research/research_checkpoint_resume.md`
- `docs/backend/simulation/research_execution_paths.md`
