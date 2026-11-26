"""The research hook's planted-controls option (research plan, "Planted controls, M1").

What is checked: the option is refused unless it is spelled right and runs on the
admission layout; the controls reach the pipeline as written, with nothing generated;
no generation draw can produce one; and a control never reaches governance, by either of
the two checks (held back from the deployable list, refused by the seeding itself).
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

import autonomous_trading_platform.application.services.platform_replay.research_hooks as rh
from autonomous_trading_platform.research.config.experiment_config import ExperimentType
from autonomous_trading_platform.research.experiments.services.experiment_orchestration_service import (
    ExperimentOrchestrationService,
)
from autonomous_trading_platform.research.pipeline.controls_report import (
    PLANTED_CONTROLS,
    planted_control_strategy_set,
)
from autonomous_trading_platform.research.strategy_generation.compatibility import (
    strategy_is_generation_compatible,
)
from autonomous_trading_platform.research.strategy_generation.generation_result import (
    GenerationOptions,
)
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from autonomous_trading_platform.strategy.implementations.planted_control import (
    is_planted_control_type,
)
from autonomous_trading_platform.strategy.registry import get_registry

_ADMISSION = {"window_layout": "admission", "dataset_version": "d", "universe_timeline": "t"}
_T1 = datetime(2026, 11, 16, 21, tzinfo=UTC)
_CONTROL = PLANTED_CONTROLS[0].strategy_id


# --- the option ---------------------------------------------------------------------------


def test_without_the_option_the_tick_runs_the_draw() -> None:
    assert rh._resolve_candidates_option(None) == rh.CANDIDATES_DRAW
    assert rh._resolve_candidates_option(_ADMISSION) == rh.CANDIDATES_DRAW


def test_the_controls_run_on_the_admission_layout() -> None:
    options = {**_ADMISSION, "candidates": "planted_controls"}
    assert rh._resolve_candidates_option(options) == rh.CANDIDATES_PLANTED_CONTROLS


def test_the_controls_are_refused_without_the_admission_layout() -> None:
    with pytest.raises(ValueError, match="needs window_layout 'admission'"):
        rh._resolve_candidates_option({"candidates": "planted_controls"})


@pytest.mark.parametrize("bad", ["controls", "Planted_Controls", "", 1, True, None])
def test_a_misspelt_option_is_an_error_never_the_draw(bad: object) -> None:
    with pytest.raises(ValueError, match="research.options.candidates must be one of"):
        rh._resolve_candidates_option({**_ADMISSION, "candidates": bad})


# --- the controls reach the pipeline as written ---------------------------------------------


def test_the_fixed_set_is_used_as_written_and_nothing_is_generated() -> None:
    """``parameter_space=None`` is the orchestration service's rule for a set used as
    written: no generator is asked (the stand-in has none) and no generation line is
    written."""
    plan = SimpleNamespace(
        experiment_type=ExperimentType.SWEEP,
        parameter_space=None,
        strategy_set=planted_control_strategy_set(),
    )

    configs, report = ExperimentOrchestrationService._expand_strategy_configs_with_report(
        SimpleNamespace(),  # type: ignore[arg-type]
        plan,  # type: ignore[arg-type]
    )

    assert [c.strategy_id for c in configs] == [c.strategy_id for c in PLANTED_CONTROLS]
    assert report == []


# --- no draw produces one ----------------------------------------------------------------


def test_no_generation_draw_includes_the_control_type() -> None:
    definition = get_registry().get_definition("planted_oracle")

    assert strategy_is_generation_compatible(definition, GenerationOptions()) == (
        False,
        "debug_excluded",
    )
    assert "planted_oracle" not in {
        d.strategy_type for d in get_registry().list_production_strategies()
    }
    assert "planted_oracle" not in {
        entry["type"] for entry in rh._replay_strategy_set(smoke=False, seed=202611)
    }


def test_only_the_control_type_is_a_planted_control() -> None:
    controls = [t for t in get_registry().list_strategy_types() if is_planted_control_type(t)]

    assert controls == ["planted_oracle"]
    assert is_planted_control_type("not_registered") is False


# --- never reaches governance: first check -------------------------------------------------


def test_controls_are_held_back_from_the_deployable_list() -> None:
    config = StrategyConfig(**planted_control_strategy_set()[0])
    deployable = [
        SimpleNamespace(strategy_id=sid)
        for sid in ("momentum__abc", _CONTROL, "planted_oracle__acc50_seed99")
    ]

    kept, withheld = rh._withhold_planted_controls(deployable, {_CONTROL: config})

    assert [s.strategy_id for s in kept] == ["momentum__abc"]
    # By its config's type, and by its id's type where no config is at hand.
    assert withheld == [_CONTROL, "planted_oracle__acc50_seed99"]


# --- never reaches governance: second check ------------------------------------------------


def _seed(session: Session, strategy_id: str, config_by_id: dict) -> None:
    rh._seed_research_governance_from_intelligence(
        session=session,
        summaries=[SimpleNamespace(strategy_id=strategy_id)],
        config_by_id=config_by_id,
        sim_by_id={},
        experiment_id="replay_research_202611",
        now_utc=_T1,
    )


def _governance_ids(session: Session) -> list[str]:
    return [row.strategy_id for row in session.query(StrategyGovernance).all()]


def test_the_seeding_refuses_a_control_it_is_handed(db_session: Session) -> None:
    config = StrategyConfig(**planted_control_strategy_set()[0])

    _seed(db_session, _CONTROL, {_CONTROL: config})
    _seed(db_session, "planted_oracle__acc50_seed11", {})
    _seed(db_session, "momentum__abc", {})

    assert _governance_ids(db_session) == ["momentum__abc"]
