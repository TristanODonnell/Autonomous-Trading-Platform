"""
RegimeGate — pure pass/fail rules over a StrategyRegimeProfile.

Question answered: "does this strategy only work in one kind of market?"

For each configured dimension (default: trend + volatility) the gate looks at
the regime buckets that have enough bars to be meaningful ("evaluable"):

  1. worst-regime Sharpe  >= min_regime_sharpe
       No evaluable regime may be catastrophic.
  2. worst-regime drawdown >= max_regime_drawdown
  3. positive-regime fraction >= min_positive_regime_fraction
       Fraction of evaluable buckets with positive total return.

A dimension with fewer than min_evaluable_regimes evaluable buckets (e.g. a
90-day window that was bull the whole time) cannot be judged, and is skipped.
If no dimension can be judged, on_insufficient_coverage decides the outcome
("pass" keeps the strategy with a warning; "fail" eliminates it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from autonomous_trading_platform.research.analysis.regimes.regime_bucket import REGIME_DIMENSIONS
from autonomous_trading_platform.research.analysis.regimes.regime_metrics import (
    RegimeConditionedMetrics,
)
from autonomous_trading_platform.research.analysis.regimes.strategy_regime_profile import (
    StrategyRegimeProfile,
)

InsufficientPolicy = Literal["pass", "fail"]


@dataclass(frozen=True)
class RegimeGateConfig:
    dimensions: tuple[str, ...] = ("trend", "volatility")
    min_bars_per_regime: int = 20
    min_evaluable_regimes: int = 2
    min_regime_sharpe: float = -0.5
    max_regime_drawdown: float = -0.25
    min_positive_regime_fraction: float = 0.5
    on_insufficient_coverage: InsufficientPolicy = "pass"

    def __post_init__(self) -> None:
        if not self.dimensions:
            raise ValueError("dimensions must not be empty")
        unknown = [d for d in self.dimensions if d not in REGIME_DIMENSIONS]
        if unknown:
            raise ValueError(f"Unknown regime dimensions {unknown}; valid: {REGIME_DIMENSIONS}")
        if self.min_bars_per_regime < 2:
            raise ValueError("min_bars_per_regime must be >= 2")
        if self.min_evaluable_regimes < 1:
            raise ValueError("min_evaluable_regimes must be >= 1")
        if self.max_regime_drawdown > 0:
            raise ValueError("max_regime_drawdown must be <= 0")
        if not (0.0 <= self.min_positive_regime_fraction <= 1.0):
            raise ValueError("min_positive_regime_fraction must be in [0, 1]")
        if self.on_insufficient_coverage not in ("pass", "fail"):
            raise ValueError("on_insufficient_coverage must be 'pass' or 'fail'")


@dataclass(frozen=True)
class RegimeDimensionCheck:
    dimension: str
    evaluable_labels: tuple[str, ...]
    worst_label: str | None
    worst_sharpe: float | None
    worst_drawdown: float | None
    positive_fraction: float | None
    evaluated: bool
    failures: tuple[str, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.failures


@dataclass(frozen=True)
class RegimeGateVerdict:
    strategy_id: str
    passed: bool
    dimension_checks: tuple[RegimeDimensionCheck, ...]
    failure_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def evaluated_dimensions(self) -> list[str]:
        return [c.dimension for c in self.dimension_checks if c.evaluated]


def _evaluable(
    metrics_by_label: dict[str, RegimeConditionedMetrics],
    min_bars: int,
) -> dict[str, RegimeConditionedMetrics]:
    return {
        label: m
        for label, m in metrics_by_label.items()
        if m.bar_count >= min_bars and m.sharpe is not None
    }


def _check_dimension(
    dimension: str,
    metrics_by_label: dict[str, RegimeConditionedMetrics],
    cfg: RegimeGateConfig,
) -> RegimeDimensionCheck:
    evaluable = _evaluable(metrics_by_label, cfg.min_bars_per_regime)
    labels = tuple(sorted(evaluable))

    if len(evaluable) < cfg.min_evaluable_regimes:
        return RegimeDimensionCheck(
            dimension=dimension,
            evaluable_labels=labels,
            worst_label=None,
            worst_sharpe=None,
            worst_drawdown=None,
            positive_fraction=None,
            evaluated=False,
        )

    worst_label, worst = min(evaluable.items(), key=lambda kv: (kv[1].sharpe, kv[0]))
    worst_sharpe = float(worst.sharpe)  # type: ignore[arg-type]
    drawdowns = [m.max_drawdown for m in evaluable.values() if m.max_drawdown is not None]
    worst_drawdown = min(drawdowns) if drawdowns else None
    n_positive = sum(
        1 for m in evaluable.values() if m.total_return is not None and m.total_return > 0
    )
    positive_fraction = n_positive / len(evaluable)

    failures: list[str] = []
    if worst_sharpe < cfg.min_regime_sharpe:
        failures.append(
            f"{dimension}:{worst_label} Sharpe {worst_sharpe:.2f} < {cfg.min_regime_sharpe:.2f}"
        )
    if worst_drawdown is not None and worst_drawdown < cfg.max_regime_drawdown:
        failures.append(
            f"{dimension} worst-regime drawdown {worst_drawdown:.2%} "
            f"< {cfg.max_regime_drawdown:.2%}"
        )
    if positive_fraction < cfg.min_positive_regime_fraction:
        failures.append(
            f"{dimension} profitable in {n_positive}/{len(evaluable)} regimes "
            f"(< {cfg.min_positive_regime_fraction:.0%})"
        )

    return RegimeDimensionCheck(
        dimension=dimension,
        evaluable_labels=labels,
        worst_label=worst_label,
        worst_sharpe=worst_sharpe,
        worst_drawdown=worst_drawdown,
        positive_fraction=positive_fraction,
        evaluated=True,
        failures=tuple(failures),
    )


def evaluate_regime_gate(
    *,
    strategy_id: str,
    profile: StrategyRegimeProfile | None,
    config: RegimeGateConfig,
    unavailable_reason: str | None = None,
) -> RegimeGateVerdict:
    """Decide whether a strategy survives the regime gate.

    profile=None means regime labels were unavailable; the insufficient
    coverage policy applies and unavailable_reason is recorded.
    """
    if profile is None:
        reason = unavailable_reason or "regime profile unavailable"
        return _insufficient(strategy_id, (), config, reason)

    checks = tuple(
        _check_dimension(dim, getattr(profile, f"by_{dim}").metrics_by_label, config)
        for dim in config.dimensions
    )
    evaluated = [c for c in checks if c.evaluated]
    if not evaluated:
        return _insufficient(
            strategy_id,
            checks,
            config,
            f"no dimension had >= {config.min_evaluable_regimes} regimes with "
            f">= {config.min_bars_per_regime} bars",
        )

    failures = tuple(f for c in evaluated for f in c.failures)
    warnings = tuple(
        f"{c.dimension}: not evaluated (only {len(c.evaluable_labels)} evaluable regime(s))"
        for c in checks
        if not c.evaluated
    )
    return RegimeGateVerdict(
        strategy_id=strategy_id,
        passed=not failures,
        dimension_checks=checks,
        failure_reasons=failures,
        warnings=warnings,
    )


def _insufficient(
    strategy_id: str,
    checks: tuple[RegimeDimensionCheck, ...],
    config: RegimeGateConfig,
    reason: str,
) -> RegimeGateVerdict:
    passed = config.on_insufficient_coverage == "pass"
    message = f"insufficient regime coverage: {reason}"
    return RegimeGateVerdict(
        strategy_id=strategy_id,
        passed=passed,
        dimension_checks=checks,
        failure_reasons=() if passed else (message,),
        warnings=(message,) if passed else (),
    )
