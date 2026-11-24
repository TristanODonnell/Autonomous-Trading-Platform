"""The research month reaches the candidate draw (plan G1, Step 1b), and the experiment
reports per family what the draw delivered (plan G2).

Before G1 the hook handed generation `n_samples` only, so every month drew with the
generator's default seed (42) and got the same 30 candidates.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
    _replay_strategy_set,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.experiments.models.experiment_plan import (
    ExperimentDefinition,
    ExperimentType,
)
from autonomous_trading_platform.research.experiments.services.experiment_orchestration_service import (
    ExperimentOrchestrationService,
)
from autonomous_trading_platform.research.strategy_generation.strategy_generation_engine import (
    StrategyGenerationEngine,
)


def _service() -> ExperimentOrchestrationService:
    # Expanding the strategy set only reads the generation engine.
    return ExperimentOrchestrationService(
        experiment_repository=cast(Any, None),
        simulation_runner=cast(Any, None),
        strategy_generation_engine=StrategyGenerationEngine(),
        filter_score_service=cast(Any, None),
    )


def _plan(month: int, *, smoke: bool = False) -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id=f"replay_research_{month}",
        experiment_type=ExperimentType.SWEEP,
        description=None,
        strategy_set=_replay_strategy_set(smoke=smoke, seed=month),
        parameter_grid=[{}],
        dataset_version="v1",
        universe_version="u1",
        price_basis=PriceBasis.RAW,
        symbols=["AAPL"],
        start_date=date(2024, 11, 18),
        end_date=date(2026, 5, 15),
        random_seed=month,
        parameter_space={},
    )


def _ids(month: int) -> set[str]:
    configs, _ = _service()._expand_strategy_configs_with_report(_plan(month))
    return {config.strategy_id for config in configs}


def test_every_family_is_handed_the_month_as_its_seed() -> None:
    for smoke in (False, True):
        strategy_set = _replay_strategy_set(smoke=smoke, seed=202611)
        assert {entry["options"]["seed"] for entry in strategy_set} == {202611}


def test_two_months_draw_different_candidates_and_one_month_draws_the_same() -> None:
    november = _ids(202611)

    assert _ids(202611) == november
    assert _ids(202612) != november


def test_the_experiment_reports_what_each_family_delivered() -> None:
    configs, report = _service()._expand_strategy_configs_with_report(_plan(202611))

    by_type = {line["strategy_type"]: line for line in report}
    assert set(by_type) == {
        "momentum",
        "mean_reversion",
        "moving_average_crossover",
        "factor_based",
        "composite_rule",
    }
    assert sum(cast(int, line["delivered"]) for line in report) == len(configs)
    # Crossover has three settings that pass its rule; six are asked for.
    assert by_type["moving_average_crossover"] == {
        "strategy_type": "moving_average_crossover",
        "method": "random",
        "requested": 6,
        "delivered": 3,
        "rejected": 3,
        "duplicates": 0,
        "exhausted": True,
    }
    for family in ("momentum", "mean_reversion", "factor_based", "composite_rule"):
        line = by_type[family]
        assert line["delivered"] == line["requested"]
        assert line["exhausted"] is False
