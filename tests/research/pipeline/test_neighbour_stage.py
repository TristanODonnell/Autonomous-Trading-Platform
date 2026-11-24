"""NeighbourStage with a scripted fake runner (plan R13).

The stage's own work: which simulations it asks for, under whose id, what it reuses,
and that a neighbour is never handed on as a survivor. The rules for building a
neighbour and for judging one are covered in tests/research/pipeline/gates/.
"""

from __future__ import annotations

from datetime import date
from typing import cast

import pytest

from autonomous_trading_platform.research.pipeline.gates.neighbour_gate import (
    NeighbourGateConfig,
    NeighbourGateVerdict,
)
from autonomous_trading_platform.research.pipeline.gates.parameter_neighbours import NudgeRule
from autonomous_trading_platform.research.pipeline.stages.base_stage import StageResult
from autonomous_trading_platform.research.pipeline.stages.neighbour_stage import (
    NEIGHBOUR_ROLE,
    OWN_RUN_ROLE,
    NeighbourStage,
    NeighbourStageConfig,
)
from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunner,
    SimulationRunRequest,
    SimulationRunResult,
)
from autonomous_trading_platform.research.strategy_generation.generators.utils import make_config
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig
from tests.research.pipeline.test_robustness_stages import (
    END,
    START,
    SYMBOLS,
    ScriptedRunner,
    _cfg,
    _run,
)

GAIN, LOSS = 0.003, -0.003


def _wobble(day: date) -> float:
    return 0.0005 if day.toordinal() % 2 else -0.0005


def _gains_whatever(_sid: str, _request: SimulationRunRequest, day: date) -> float:
    """A plateau: the result does not depend on the settings."""
    return GAIN + _wobble(day)


def _gains_only_at_ten_bars(_sid: str, request: SimulationRunRequest, day: date) -> float:
    """A spike: the result is there at a lookback of exactly 10 bars and nowhere else."""
    lookback = request.strategy_config["parameters"]["lookback"]
    return (GAIN if lookback == 10 else LOSS) + _wobble(day)


def _momentum(lookback: int = 10) -> StrategyConfig:
    return make_config("momentum", {"lookback": lookback, "buy_above": 0.05, "sell_below": -0.05})


def _stage(runner: ScriptedRunner, **gate: object) -> NeighbourStage:
    return NeighbourStage(
        stage_config=NeighbourStageConfig(
            name="neighbours",
            symbols=SYMBOLS,
            start_date=START,
            end_date=END,
            gate=NeighbourGateConfig(**gate),  # type: ignore[arg-type]
        ),
        simulation_runner=cast(SimulationRunner, runner),
    )


def _verdict(result: StageResult, strategy_id: str) -> NeighbourGateVerdict:
    verdict = result.diagnostics[strategy_id].neighbour_verdict
    assert verdict is not None
    return verdict


def _asked(runner: ScriptedRunner, role: str) -> list[SimulationRunRequest]:
    return [request for request in runner.requests if request.window_role == role]


# --------------------------------------------------------------------------------------
# What the stage finds
# --------------------------------------------------------------------------------------


def test_a_result_that_is_there_at_one_setting_only_is_found_and_the_strategy_is_kept() -> None:
    spike = _momentum()
    result = _run(_stage(ScriptedRunner(_gains_only_at_ten_bars)), [spike])

    verdict = _verdict(result, spike.strategy_id)
    assert {check.setting: check.fragile for check in verdict.settings} == {
        "lookback": True,
        # Their neighbours keep the lookback of 10 bars, and so keep the result.
        "buy_above": False,
        "sell_below": False,
    }
    (finding,) = verdict.findings
    assert finding.startswith("lookback = 10: neither neighbour holds (at 8: Sharpe -")
    # Report only: found, written down, and the strategy goes on.
    assert verdict.enforced is False
    assert result.survivors == [spike]
    (output,) = result.filter_outputs
    assert output.filter_result.passed is True and output.filter_result.failures == []


def test_enforced_the_same_finding_removes_the_strategy() -> None:
    spike = _momentum()
    result = _run(_stage(ScriptedRunner(_gains_only_at_ten_bars), enforce=True), [spike])

    verdict = _verdict(result, spike.strategy_id)
    assert result.survivors == []
    (output,) = result.filter_outputs
    assert output.filter_result.passed is False
    assert output.filter_result.failures == list(verdict.findings) != []


def test_a_result_that_does_not_depend_on_the_settings_has_no_finding() -> None:
    plateau = _momentum()
    result = _run(_stage(ScriptedRunner(_gains_whatever), enforce=True), [plateau])

    verdict = _verdict(result, plateau.strategy_id)
    assert verdict.findings == () and verdict.holding_fraction == 1.0
    assert len(verdict.results) == 6 and verdict.skipped == ()
    assert verdict.own_sharpe is not None and verdict.own_sharpe > 0
    assert result.survivors == [plateau]


# --------------------------------------------------------------------------------------
# What it asks the runner for
# --------------------------------------------------------------------------------------


def test_each_neighbour_is_run_once_over_the_stages_window_under_an_id_of_its_own() -> None:
    config = _momentum()
    runner = ScriptedRunner(_gains_whatever)

    _run(_stage(runner), [config])

    neighbours = _asked(runner, NEIGHBOUR_ROLE)
    assert len(neighbours) == 6
    assert len({request.strategy_id for request in neighbours}) == 6
    for request in neighbours:
        assert request.strategy_id != config.strategy_id
        assert (request.start_date, request.end_date) == (START, END)
        assert request.symbols == SYMBOLS and request.cost_multiplier == 1.0
        assert request.stage_name == "neighbours"
        # The id is the one this exact strategy has anywhere: its type and its hash.
        parameters = request.strategy_config["parameters"]
        assert request.strategy_id == make_config("momentum", parameters).strategy_id
        differing = [name for name, value in parameters.items() if value != config.parameters[name]]
        assert len(differing) == 1


def test_no_run_under_the_strategys_own_id_has_settings_other_than_its_own() -> None:
    """The runner stores a run's config under the run's strategy id."""
    config = _momentum()
    runner = ScriptedRunner(_gains_whatever)

    _run(_stage(runner), [config])

    own = [request for request in runner.requests if request.strategy_id == config.strategy_id]
    assert [request.window_role for request in own] == [OWN_RUN_ROLE]
    assert own[0].strategy_config["parameters"] == config.parameters


def test_the_strategys_own_run_is_taken_from_an_earlier_stage_when_one_covers_the_window() -> None:
    config = _momentum()
    first = _run(_stage(ScriptedRunner(_gains_whatever)), [config])
    earlier_run = first.diagnostics[config.strategy_id].reference_result
    assert earlier_run is not None

    runner = ScriptedRunner(_gains_whatever)
    stage = _stage(runner)
    stage.bind_prior_results([first])
    result = _run(stage, [config])

    assert _asked(runner, OWN_RUN_ROLE) == []
    assert len(_asked(runner, NEIGHBOUR_ROLE)) == 6
    assert result.diagnostics[config.strategy_id].reference_result is earlier_run
    assert _verdict(result, config.strategy_id).own_sharpe == pytest.approx(
        float(earlier_run.risk_metrics.sharpe_ratio)
    )


def test_an_earlier_run_over_other_days_is_not_taken_for_the_strategys_own() -> None:
    config = _momentum()
    shorter = NeighbourStage(
        stage_config=NeighbourStageConfig(
            name="neighbours", symbols=SYMBOLS, start_date=START, end_date=date(2024, 2, 29)
        ),
        simulation_runner=cast(SimulationRunner, ScriptedRunner(_gains_whatever)),
    )
    earlier = _run(shorter, [config])

    runner = ScriptedRunner(_gains_whatever)
    stage = _stage(runner)
    stage.bind_prior_results([earlier])
    _run(stage, [config])

    assert len(_asked(runner, OWN_RUN_ROLE)) == 1


def test_two_survivors_with_the_same_settings_share_their_neighbours_runs() -> None:
    """A neighbour is one strategy however many survivors it is a neighbour of."""
    runner = ScriptedRunner(_gains_whatever)

    result = _run(_stage(runner), [_cfg("a"), _cfg("b")])

    assert len(_asked(runner, NEIGHBOUR_ROLE)) == 4
    assert len(_asked(runner, OWN_RUN_ROLE)) == 2
    for strategy_id in ("a", "b"):
        verdict = _verdict(result, strategy_id)
        assert [(r.setting, r.value) for r in verdict.results] == [
            ("short_window", 4),
            ("short_window", 6),
            ("long_window", 16),
            ("long_window", 24),
        ]


# --------------------------------------------------------------------------------------
# A neighbour is evidence about a survivor, and nothing else
# --------------------------------------------------------------------------------------


def test_a_neighbour_is_never_a_survivor_and_has_no_verdict_of_its_own() -> None:
    survivors = [_momentum(10), _momentum(20)]
    ids = [config.strategy_id for config in survivors]
    runner = ScriptedRunner(_gains_whatever)

    result = _run(_stage(runner), survivors)

    assert result.survivors == survivors
    assert list(result.diagnostics) == ids
    assert [output.strategy_id for output in result.filter_outputs] == ids
    # Their runs are the stage's runs, with the survivors' own.
    assert len(result.simulation_results) == len(runner.requests) == 2 + 12
    for diagnostics in result.diagnostics.values():
        assert diagnostics.reference_result is not None
        assert diagnostics.reference_result.strategy_id in ids


def test_a_neighbour_that_could_not_be_built_is_not_run_and_is_kept_with_its_reason() -> None:
    at_the_limit = _momentum(60)
    runner = ScriptedRunner(_gains_whatever)

    result = _run(_stage(runner), [at_the_limit])

    verdict = _verdict(result, at_the_limit.strategy_id)
    assert [(s.setting, s.direction, s.value, s.reason) for s in verdict.skipped] == [
        ("lookback", "up", 72, "above the declared maximum 60")
    ]
    assert len(verdict.results) == len(_asked(runner, NEIGHBOUR_ROLE)) == 5
    assert all(
        request.strategy_config["parameters"]["lookback"] <= 60 for request in runner.requests
    )


def test_a_neighbour_whose_run_gave_no_result_is_a_finding() -> None:
    class _NoResultAtEightBars(ScriptedRunner):
        def run(self, request: SimulationRunRequest) -> SimulationRunResult:
            if request.strategy_config["parameters"]["lookback"] == 8:
                self.requests.append(request)
                return None  # type: ignore[return-value]  # as a resume dry-run returns
            return super().run(request)

    config = _momentum()
    result = _run(_stage(_NoResultAtEightBars(_gains_whatever)), [config])

    verdict = _verdict(result, config.strategy_id)
    assert verdict.findings == ("no result for 1 neighbour run(s) that were asked for",)
    assert len(verdict.results) == 5
    assert result.survivors == [config]


def test_no_survivors_is_an_empty_result_and_nothing_is_run() -> None:
    runner = ScriptedRunner(_gains_whatever)

    result = _run(_stage(runner), [])

    assert result.survivors == [] and result.diagnostics == {}
    assert runner.requests == []


def test_the_nudge_rule_is_the_stage_configs() -> None:
    config = _momentum()
    runner = ScriptedRunner(_gains_whatever)
    stage = NeighbourStage(
        stage_config=NeighbourStageConfig(
            name="neighbours",
            symbols=SYMBOLS,
            start_date=START,
            end_date=END,
            nudge=NudgeRule(window_fraction=0.5),
        ),
        simulation_runner=cast(SimulationRunner, runner),
    )

    _run(stage, [config])

    lookbacks = {r.strategy_config["parameters"]["lookback"] for r in runner.requests}
    assert lookbacks == {5, 10, 15}


# --------------------------------------------------------------------------------------
# Config, registry, and the holdout rule
# --------------------------------------------------------------------------------------

WINDOW = {"start_date": "2024-01-02", "end_date": "2024-03-29", "symbols": ["aaa"]}


def _load(**raw: object) -> NeighbourStage:
    runner = cast(SimulationRunner, ScriptedRunner(_gains_whatever))
    stage = StageRegistry.load({"type": "neighbour", "name": "n", **WINDOW, **raw}, runner)
    assert isinstance(stage, NeighbourStage)
    return stage


def test_the_stage_loads_from_yaml_report_only_unless_told() -> None:
    assert "neighbour" in StageRegistry.registered_types()

    as_is = _load()
    told = _load(
        enforce=True, min_share_of_own_sharpe=0.8, min_neighbour_sharpe=0.3, window_fraction=0.3
    )

    assert as_is.stage_name == "n" and as_is._cfg.symbols == ["AAA"]
    assert as_is._cfg.gate == NeighbourGateConfig() and as_is._cfg.gate.enforce is False
    assert as_is._cfg.nudge == NudgeRule()
    assert told._cfg.gate == NeighbourGateConfig(
        min_share_of_own_sharpe=0.8, min_neighbour_sharpe=0.3, enforce=True
    )
    assert told._cfg.nudge == NudgeRule(window_fraction=0.3)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"window_fraction": 0.0}, "a nudge fraction must be in"),
        ({"range_fraction": 1.0}, "a nudge fraction must be in"),
        ({"min_share_of_own_sharpe": 1.5}, "min_share_of_own_sharpe must be in"),
        ({"end_date": "2024-01-02"}, "must be strictly before end_date"),
    ],
)
def test_values_that_mean_nothing_fail_when_the_stage_is_loaded(
    raw: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        _load(**raw)


def test_the_stage_tells_the_holdout_rule_the_last_day_it_reads() -> None:
    facts = _stage(ScriptedRunner(_gains_whatever)).validation_facts()

    assert facts.stage_name == "neighbours" and facts.last_day_read == END


def test_a_stage_config_with_no_days_or_no_symbols_is_refused() -> None:
    with pytest.raises(ValueError, match="start_date must be before end_date"):
        NeighbourStageConfig(name="n", symbols=SYMBOLS, start_date=END, end_date=START)
    with pytest.raises(ValueError, match="symbols must not be empty"):
        NeighbourStageConfig(name="n", symbols=[], start_date=START, end_date=END)
