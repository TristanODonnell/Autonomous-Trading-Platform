"""
StressGate — pure pass/fail rules for the stress stage.

Two evidence sources, with different roles:

1. Execution-cost re-simulations — the pass rule. The strategy is re-run with
   slippage and commission scaled by each multiplier (e.g. 2x, 3x). This
   catches strategies whose edge only exists under optimistic fills —
   something a transform of the original equity curve cannot see, because
   higher costs change which trades are profitable.

2. Equity-curve shock scenarios (StressTestService) — reported, never judged.
   The strategy's realised equity curve is transformed (vol spike, one-time
   crash, downside amplification, trend reversal, per-bar cost drag) and
   re-scored. The numbers stay on the verdict for the report. They are not
   part of the pass rule because on intraday bars they do not separate
   strategies: scaling returns cannot change Sharpe, a sign flip fails every
   profitable strategy, the per-bar scenarios assume daily bars, and a
   one-time −5% / −10% shock is survived only by a strategy that earned more
   than that inside the window. On the real-bars run every profitable
   strategy survived the same 2 of 7 (plan item R11).

A strategy survives iff every requested cost multiplier produced a result and
    cost survival rate >= min_cost_survival_rate

A cost run survives when it traded, its Sharpe is >= min_cost_sharpe and its
drawdown is >= max_cost_drawdown. A run with no trades does not survive: a
flat curve has Sharpe 0.0 and would otherwise meet a threshold of 0.0. A
requested multiplier with no result fails the strategy; the gate does not pass
on evidence it asked for and did not get.

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
    # Shock scenarios (equity-curve transforms). Report only: the two thresholds
    # set each scenario's "survived" flag in the report, not the verdict.
    shock_scenarios: tuple[str, ...] = tuple(s.name for s in BUILT_IN_SCENARIOS)
    min_shock_sharpe: float = 0.0
    max_shock_drawdown: float = -0.40
    # Execution-cost re-simulations: the pass rule.
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
        if not self.cost_multipliers:
            raise ValueError(
                "cost_multipliers must not be empty: the cost re-simulations are the "
                "stress gate's only pass rule"
            )
        if any(m <= 1.0 for m in self.cost_multipliers):
            raise ValueError("cost_multipliers must each be > 1.0 (1.0 is the baseline run)")
        if self.max_shock_drawdown > 0 or self.max_cost_drawdown > 0:
            raise ValueError("drawdown thresholds must be <= 0")
        if not (0.0 <= self.min_cost_survival_rate <= 1.0):
            raise ValueError(
                f"min_cost_survival_rate must be in [0, 1], got {self.min_cost_survival_rate}"
            )


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
            self.trade_count > 0
            and self.sharpe >= config.min_cost_sharpe
            and self.max_drawdown >= config.max_cost_drawdown
        )

    def describe(self) -> str:
        if self.trade_count <= 0:
            return f"{self.cost_multiplier:g}x costs: no trades"
        return (
            f"{self.cost_multiplier:g}x costs: Sharpe {self.sharpe:.2f}, DD {self.max_drawdown:.2%}"
        )


@dataclass(frozen=True)
class StressGateVerdict:
    """Gate decision. ``shock_*`` fields are report-only and never affect ``passed``."""

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

    # Shocks: carried on the verdict for the report, never a failure.
    shock_rate: float | None = None
    if shock_summary is not None and shock_summary.n_scenarios > 0:
        shock_rate = shock_summary.survival_rate
    elif config.shock_scenarios:
        warnings.append("shock scenarios not reported (no usable equity curve)")

    # Costs: the pass rule. Every requested multiplier must have a result.
    produced = {r.cost_multiplier for r in cost_results}
    missing = [m for m in config.cost_multipliers if m not in produced]
    if missing:
        failures.append(
            "no result for the "
            + ", ".join(f"{m:g}x" for m in missing)
            + " cost re-simulation(s); the gate does not pass without them"
        )

    cost_rate: float | None = None
    if cost_results:
        survived = [r for r in cost_results if r.survived(config)]
        cost_rate = len(survived) / len(cost_results)
        if cost_rate < config.min_cost_survival_rate:
            detail = "; ".join(r.describe() for r in cost_results if not r.survived(config))
            failures.append(
                f"survived {len(survived)}/{len(cost_results)} cost scenarios "
                f"(< {config.min_cost_survival_rate:.0%}); {detail}"
            )

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
