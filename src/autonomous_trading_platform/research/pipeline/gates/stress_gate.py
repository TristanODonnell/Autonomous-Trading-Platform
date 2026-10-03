"""
StressGate — pure pass/fail rules for the stress stage.

Two independent evidence sources:

1. Equity-curve shock scenarios (StressTestService): the strategy's realised
   equity curve is transformed (vol spike, one-time crash, downside
   amplification, trend reversal, per-bar cost drag) and re-scored. Cheap — no
   re-simulation — but the strategy's trades are held fixed.

2. Execution-cost re-simulations: the strategy is re-run with slippage and
   commission scaled by each multiplier (e.g. 2x, 3x). This catches strategies
   whose edge only exists under optimistic fills — something a transform of
   the original equity curve cannot see, because higher costs change which
   trades are profitable.

A strategy survives iff
    shock survival rate >= min_shock_survival_rate   AND
    cost  survival rate >= min_cost_survival_rate

Either check is skipped (and noted as a warning) when it has no evidence,
e.g. cost_multipliers=() disables the cost re-runs.

Bar-perturbation re-simulation (injecting gaps/shocks into the price data so
the strategy trades through them) is intentionally not implemented yet — see
the known-gaps section of docs/backend/research/research_robustness_stages.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from autonomous_trading_platform.research.validation.stress_test_service import (
    BUILT_IN_SCENARIOS,
    StressTestSummary,
)

_BUILT_IN_NAMES = frozenset(s.name for s in BUILT_IN_SCENARIOS)


@dataclass(frozen=True)
class StressGateConfig:
    # Shock scenarios (equity-curve transforms)
    shock_scenarios: tuple[str, ...] = tuple(s.name for s in BUILT_IN_SCENARIOS)
    min_shock_sharpe: float = 0.0
    max_shock_drawdown: float = -0.40
    min_shock_survival_rate: float = 0.5
    # Execution-cost re-simulations
    cost_multipliers: tuple[float, ...] = (2.0, 3.0)
    min_cost_sharpe: float = 0.0
    max_cost_drawdown: float = -0.40
    min_cost_survival_rate: float = 0.5

    def __post_init__(self) -> None:
        unknown = [n for n in self.shock_scenarios if n not in _BUILT_IN_NAMES]
        if unknown:
            raise ValueError(
                f"Unknown stress scenarios {unknown}; valid: {sorted(_BUILT_IN_NAMES)}"
            )
        if any(m <= 1.0 for m in self.cost_multipliers):
            raise ValueError("cost_multipliers must each be > 1.0 (1.0 is the baseline run)")
        if self.max_shock_drawdown > 0 or self.max_cost_drawdown > 0:
            raise ValueError("drawdown thresholds must be <= 0")
        for name in ("min_shock_survival_rate", "min_cost_survival_rate"):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be in [0, 1], got {v}")


@dataclass(frozen=True)
class CostStressResult:
    """Metrics of one execution-cost re-simulation."""

    cost_multiplier: float
    sharpe: float
    max_drawdown: float
    total_return: float
    trade_count: int

    def survived(self, config: StressGateConfig) -> bool:
        return (
            self.sharpe >= config.min_cost_sharpe and self.max_drawdown >= config.max_cost_drawdown
        )


@dataclass(frozen=True)
class StressGateVerdict:
    strategy_id: str
    passed: bool
    shock_survival_rate: float | None
    cost_survival_rate: float | None
    shock_summary: StressTestSummary | None
    cost_results: tuple[CostStressResult, ...]
    failure_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)


def evaluate_stress_gate(
    *,
    strategy_id: str,
    shock_summary: StressTestSummary | None,
    cost_results: list[CostStressResult],
    config: StressGateConfig,
) -> StressGateVerdict:
    failures: list[str] = []
    warnings: list[str] = []

    shock_rate: float | None = None
    if shock_summary is not None and shock_summary.n_scenarios > 0:
        shock_rate = shock_summary.survival_rate
        if shock_rate < config.min_shock_survival_rate:
            failed = [r.scenario_name for r in shock_summary.scenario_results if not r.survived]
            failures.append(
                f"survived {shock_summary.n_survived}/{shock_summary.n_scenarios} shock "
                f"scenarios (< {config.min_shock_survival_rate:.0%}); failed: {', '.join(failed)}"
            )
    elif config.shock_scenarios:
        warnings.append("shock scenarios not evaluated (no usable equity curve)")

    cost_rate: float | None = None
    if cost_results:
        survived = [r for r in cost_results if r.survived(config)]
        cost_rate = len(survived) / len(cost_results)
        if cost_rate < config.min_cost_survival_rate:
            detail = "; ".join(
                f"{r.cost_multiplier:g}x costs: Sharpe {r.sharpe:.2f}, DD {r.max_drawdown:.2%}"
                for r in cost_results
                if not r.survived(config)
            )
            failures.append(
                f"survived {len(survived)}/{len(cost_results)} cost scenarios "
                f"(< {config.min_cost_survival_rate:.0%}); {detail}"
            )
    elif config.cost_multipliers:
        warnings.append("cost re-simulations produced no results")

    return StressGateVerdict(
        strategy_id=strategy_id,
        passed=not failures,
        shock_survival_rate=shock_rate,
        cost_survival_rate=cost_rate,
        shock_summary=shock_summary,
        cost_results=tuple(cost_results),
        failure_reasons=tuple(failures),
        warnings=tuple(warnings),
    )
