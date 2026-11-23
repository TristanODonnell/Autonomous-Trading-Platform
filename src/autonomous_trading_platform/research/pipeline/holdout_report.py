"""The holdout report: each final survivor run once over the holdout, judged by nothing.

The holdout is every session from ``research/pipeline/holdout.py``'s date on. No stage
of the pipeline reads it (the validator's holdout rule). After the stages have chosen
their survivors, each survivor is simulated once over the holdout sessions there are so
far and its numbers are reported. Nobody is eliminated by them (decided 2026-11-15).

The run leaves no row in the system of record (``record_run=False``; decided
2026-11-20). Promotion, demotion, allocation and the catalog read the newest run or
metrics row of a strategy; a holdout run saved there, being the newest, would be read as
the strategy's record and a bad holdout would demote it: a gate by the back door. The
numbers go in the research step's summary instead.

This module is not a stage: a stage's output is the next stage's survivor list, and
code that is not in that chain cannot remove anyone from it.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.execution import (
    DeterministicSeedInputs,
    DeterministicSeedService,
)
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
    SimulationRunResult,
)

if TYPE_CHECKING:
    from autonomous_trading_platform.contracts.common.enums import PriceBasis
    from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig

    from .admission_window import SessionSpan

logger = get_logger(__name__)

# What a holdout run is recorded under in its seed and its artifacts' path.
HOLDOUT_STAGE_NAME = "holdout_report"
HOLDOUT_WINDOW_ROLE = "holdout"


class _Runner(Protocol):
    def run(self, request: SimulationRunRequest) -> SimulationRunResult | None: ...


@dataclass(frozen=True)
class HoldoutRunSettings:
    """What every holdout run of one research tick shares: the pipeline's own settings
    and the universe of the holdout's dates."""

    experiment_id: str
    dataset_version: str
    random_seed: int
    price_basis: PriceBasis
    initial_cash: float
    resample_to_daily: bool
    symbols: list[str]
    universe_timeline_version: str | None


def holdout_not_scored(span: SessionSpan | None, reason: str) -> dict[str, Any]:
    """The report of a tick whose survivors were not run over the holdout."""
    return {**_span_fields(span), "scored": 0, "candidates": [], "not_scored": reason}


def score_holdout(
    *,
    simulation_runner: _Runner,
    survivors: Sequence[StrategyConfig],
    span: SessionSpan,
    settings: HoldoutRunSettings,
) -> dict[str, Any]:
    """Run each survivor once over ``span`` and report its numbers.

    A run that fails is reported as that candidate's ``error`` and logged; the others
    are still run and nothing is raised: the report decides nothing, so it must not be
    able to fail the research step either.
    """
    seeds = DeterministicSeedService()
    candidates: list[dict[str, Any]] = []
    for config in survivors:
        request = SimulationRunRequest(
            experiment_id=settings.experiment_id,
            strategy_id=config.strategy_id,
            strategy_config=config.model_dump(),
            dataset_version=settings.dataset_version,
            random_seed=seeds.derive_seed(
                DeterministicSeedInputs(
                    base_seed=settings.random_seed,
                    experiment_id=settings.experiment_id,
                    strategy_id=config.strategy_id,
                    config_hash=config.config_hash(),
                    stage_name=HOLDOUT_STAGE_NAME,
                    window_role=HOLDOUT_WINDOW_ROLE,
                )
            ),
            price_basis=settings.price_basis,
            symbols=settings.symbols,
            universe_timeline_version=settings.universe_timeline_version,
            start_date=span.first,
            end_date=span.last,
            initial_cash=settings.initial_cash,
            window_role=HOLDOUT_WINDOW_ROLE,
            stage_name=HOLDOUT_STAGE_NAME,
            resample_to_daily=settings.resample_to_daily,
            record_run=False,
        )
        try:
            result = simulation_runner.run(request)
        except Exception as exc:
            logger.warning(
                "holdout_report: run failed for strategy %s", config.strategy_id, exc_info=True
            )
            candidates.append({"strategy_id": config.strategy_id, "error": str(exc)})
            continue
        if result is None:
            candidates.append({"strategy_id": config.strategy_id, "error": "no result"})
            continue
        candidates.append(
            {
                "strategy_id": config.strategy_id,
                "sharpe": _number(result.risk_metrics.sharpe_ratio),
                "total_return": _number(result.return_metrics.total_return),
                "max_drawdown": _number(result.risk_metrics.max_drawdown),
                "trade_count": result.trade_metrics.total_trades,
            }
        )

    scored = sum(1 for candidate in candidates if "error" not in candidate)
    logger.info(
        "holdout_report | %s to %s | %d session(s) | scored=%d of %d | %s",
        span.first,
        span.last,
        span.sessions,
        scored,
        len(candidates),
        "; ".join(
            f"{c['strategy_id']}: error"
            if "error" in c
            else f"{c['strategy_id']}: sharpe={c['sharpe']} return={c['total_return']} "
            f"drawdown={c['max_drawdown']} trades={c['trade_count']}"
            for c in candidates
        ),
    )
    return {**_span_fields(span), "scored": scored, "candidates": candidates, "not_scored": None}


def _span_fields(span: SessionSpan | None) -> dict[str, Any]:
    if span is None:
        return {"first": None, "last": None, "sessions": 0}
    return {
        "first": span.first.isoformat(),
        "last": span.last.isoformat(),
        "sessions": span.sessions,
    }


def _number(value: float | None) -> float | None:
    """A metric as the summary carries it: None when there is none or it is not finite
    (a Sharpe over a flat curve), since the summary is written out as JSON."""
    if value is None or not math.isfinite(value):
        return None
    return float(value)
