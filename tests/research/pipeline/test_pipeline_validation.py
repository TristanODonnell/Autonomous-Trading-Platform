"""Rules a staged pipeline must satisfy before it runs (plan items R6 and R2).

Stages are built the way production builds them (StageRegistry.load on a dict,
or the config classes directly), so the rules are exercised on both paths.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
import yaml

from autonomous_trading_platform.research.experiments.filtering.config import (
    FilterConfig,
    ScoringWeights,
)
from autonomous_trading_platform.research.pipeline.holdout import HOLDOUT_START
from autonomous_trading_platform.research.pipeline.pipeline_runner import StagedPipelineConfig
from autonomous_trading_platform.research.pipeline.pipeline_validation import (
    PipelineConfigError,
    pipeline_issues,
)
from autonomous_trading_platform.research.pipeline.stages.stage_registry import StageRegistry
from autonomous_trading_platform.research.pipeline.stages.walk_forward_stage import (
    WalkForwardStageConfig,
)
from autonomous_trading_platform.research.simulation.simulation_runner import SimulationRunner

REPO_ROOT = Path(__file__).resolve().parents[3]
RUNNER = cast(SimulationRunner, MagicMock())
REAL_FILTER = {"min_sharpe": 0.5, "min_trades": 10}
VACUOUS_FILTER = {"min_sharpe": -99.0, "min_trades": 0}


def _sim(name: str, start: str, end: str, filter_config: dict | None = None) -> dict[str, Any]:
    return {
        "type": "simulation",
        "name": name,
        "symbols": ["SPY"],
        "start_date": start,
        "end_date": end,
        "filter_config": filter_config or REAL_FILTER,
    }


def _wf(start: str = "2022-01-01", end: str = "2025-03-31", **overrides: Any) -> dict[str, Any]:
    return {
        "type": "walk_forward",
        "name": "wf",
        "symbols": ["SPY"],
        "start_date": start,
        "end_date": end,
        "train_days": 365,
        "test_days": 90,
        "step_days": 90,
        "require_all_folds": False,
        "min_folds_passed": 6,
        "train_filter_config": REAL_FILTER,
        "test_filter_config": REAL_FILTER,
        **overrides,
    }


def _stages(*raws: dict[str, Any]) -> list:
    return [StageRegistry.load(raw, RUNNER) for raw in raws]


def _wf_config(**overrides: Any) -> WalkForwardStageConfig:
    """Built in Python, the way the replay hook does it (no YAML model in between)."""
    kwargs: dict[str, Any] = {
        "name": "wf",
        "symbols": ["SPY"],
        "start_date": date(2026, 8, 15),
        "end_date": date(2026, 11, 13),
        "train_days": 45,
        "test_days": 30,
        "allow_short_test_folds": True,
        "step_days": 30,
        "train_filter_config": FilterConfig(),
        "train_scoring_weights": ScoringWeights(),
        "test_filter_config": FilterConfig(),
        "test_scoring_weights": ScoringWeights(),
        "require_all_folds": False,
        "min_folds_passed": 1,
    }
    return WalkForwardStageConfig(**{**kwargs, **overrides})


class TestRangeGuard:
    def test_selection_that_reads_into_the_test_folds_is_rejected(self) -> None:
        # The layout ma_sweep_staged.yaml had until 2026-11: both selection
        # stages end after the first test fold starts (2023-01-01).
        stages = _stages(
            _sim("cheap", "2023-07-01", "2024-12-31"),
            _sim("intermediate", "2022-01-01", "2025-03-31"),
            _wf(),
        )
        with pytest.raises(PipelineConfigError) as err:
            StagedPipelineConfig(stages=stages)
        assert len(err.value.issues) == 2
        assert "'cheap' reads up to 2024-12-31" in err.value.issues[0]
        assert "'intermediate' reads up to 2025-03-31" in err.value.issues[1]
        assert "2023-01-01" in err.value.issues[0]

    def test_selection_that_ends_the_day_before_the_first_test_fold_passes(self) -> None:
        stages = _stages(_sim("select", "2022-01-01", "2022-12-31"), _wf())
        assert StagedPipelineConfig(stages=stages).stages == stages

    def test_selection_ending_on_the_first_test_day_is_rejected(self) -> None:
        stages = _stages(_sim("select", "2022-01-01", "2023-01-01"), _wf())
        with pytest.raises(PipelineConfigError, match="reads up to 2023-01-01"):
            StagedPipelineConfig(stages=stages)

    def test_a_simulation_stage_after_walk_forward_is_not_a_selection_for_it(self) -> None:
        stages = _stages(_wf(), _sim("later", "2022-01-01", "2025-03-31"))
        assert pipeline_issues(stages) == []


class TestVacuousFilter:
    def test_a_filter_that_asks_for_nothing_is_rejected(self) -> None:
        stages = _stages(_sim("passthrough", "2024-01-01", "2024-06-30", VACUOUS_FILTER))
        with pytest.raises(PipelineConfigError, match="'passthrough' filter_config asks for no"):
            StagedPipelineConfig(stages=stages)

    def test_walk_forward_filters_are_checked_too(self) -> None:
        stages = _stages(_wf(test_filter_config=VACUOUS_FILTER))
        issues = pipeline_issues(stages)
        assert len(issues) == 1 and "test_filter_config" in issues[0]

    def test_zero_sharpe_with_a_trade_minimum_is_not_vacuous(self) -> None:
        loose = {"min_sharpe": 0.0, "min_trades": 3}
        assert pipeline_issues(_stages(_sim("cheap", "2022-01-01", "2022-12-31", loose))) == []

    def test_smoke_waives_the_cross_stage_rules(self) -> None:
        stages = _stages(
            _sim("passthrough", "2022-01-01", "2025-03-31", VACUOUS_FILTER),
            _wf(),
        )
        assert pipeline_issues(stages) != []
        assert StagedPipelineConfig(stages=stages, smoke=True).smoke is True


class TestWalkForwardGeometry:
    """Single-stage rules, enforced by the config class itself."""

    def test_overlapping_test_folds_are_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"step_days \(15\) < test_days \(30\)"):
            _wf_config(step_days=15)

    def test_overlap_is_accepted_when_named(self) -> None:
        cfg = _wf_config(step_days=15, allow_overlapping_test_folds=True)
        folds = cfg.fold_windows()
        assert len(folds) == 2
        # Fold 0 tests 2026-09-29..10-29 and fold 1 starts testing on 10-14.
        assert folds[0][3] > folds[1][2]

    def test_step_equal_to_test_gives_adjacent_test_folds(self) -> None:
        cfg = _wf_config(end_date=date(2027, 2, 13), step_days=30)
        folds = cfg.fold_windows()
        assert len(folds) >= 2
        assert all(folds[i][3] == folds[i + 1][2] for i in range(len(folds) - 1))

    def test_min_folds_passed_above_the_fold_count_is_rejected(self) -> None:
        # 90 days with 45 / 30 / 30 holds one fold.
        with pytest.raises(ValueError, match=r"min_folds_passed \(2\) > the 1 fold"):
            _wf_config(min_folds_passed=2)

    def test_min_folds_passed_is_ignored_when_all_folds_are_required(self) -> None:
        assert _wf_config(min_folds_passed=2, require_all_folds=True).min_folds_passed == 2

    def test_the_yaml_path_meets_the_same_rules(self) -> None:
        with pytest.raises(ValueError, match="step_days"):
            StageRegistry.load(_wf(step_days=45), RUNNER)
        with pytest.raises(ValueError, match="min_folds_passed"):
            StageRegistry.load(_wf(min_folds_passed=10), RUNNER)
        loaded = StageRegistry.load(_wf(step_days=45, allow_overlapping_test_folds=True), RUNNER)
        facts = loaded.validation_facts()
        assert facts is not None and facts.first_test_start == date(2023, 1, 1)


class TestExperimentFiles:
    """The experiment files in the repo load under the rules."""

    @staticmethod
    def _load(name: str, *, smoke: bool | None = None) -> StagedPipelineConfig:
        raw = yaml.safe_load((REPO_ROOT / "experiments" / "staged" / name).read_text())
        pipeline = raw["staged_pipeline_config"]
        stages = _stages(*pipeline["stages"])
        declared = bool(pipeline.get("smoke", False))
        return StagedPipelineConfig(stages=stages, smoke=declared if smoke is None else smoke)

    def test_ma_sweep_staged_is_valid(self) -> None:
        config = self._load("ma_sweep_staged.yaml")
        assert config.smoke is False
        assert pipeline_issues(config.stages) == []

    def test_mc_smoke_test_declares_itself_smoke_and_fails_without_it(self) -> None:
        assert self._load("exp_mc_smoke_test.yaml").smoke is True
        with pytest.raises(PipelineConfigError, match="asks for no evidence"):
            self._load("exp_mc_smoke_test.yaml", smoke=False)


class TestHoldoutRule:
    """Given a holdout date, no stage reads that day or a later one."""

    # The walk-forward stage of _wf(): 365 / 90 / 90 from 2022-01-01 to 2025-03-31 holds
    # nine folds, and the last one tests up to 2025-03-21.
    LAST_TEST_DAY = date(2025, 3, 21)

    def test_a_stage_that_reads_the_holdout_date_is_rejected(self) -> None:
        stages = _stages(_sim("select", "2022-01-01", "2022-12-31"))
        with pytest.raises(PipelineConfigError) as err:
            StagedPipelineConfig(stages=stages, holdout_start=date(2022, 12, 31))
        assert err.value.issues == (
            "stage 'select' reads up to 2022-12-31; the holdout starts on 2022-12-31 and no "
            "stage may read that day or a later one",
        )

    def test_a_stage_that_stops_the_day_before_passes(self) -> None:
        stages = _stages(_sim("select", "2022-01-01", "2022-12-31"))
        config = StagedPipelineConfig(stages=stages, holdout_start=date(2023, 1, 1))
        assert config.holdout_start == date(2023, 1, 1)

    def test_walk_forward_is_judged_by_its_last_test_day(self) -> None:
        """Not by the stage's end date: days after the last fold that fits are not read."""
        stages = _stages(_wf())
        facts = stages[0].validation_facts()
        assert facts is not None and facts.last_day_read == self.LAST_TEST_DAY

        issues = pipeline_issues(stages, holdout_start=self.LAST_TEST_DAY)
        assert len(issues) == 1 and "'wf' reads up to 2025-03-21" in issues[0]
        assert pipeline_issues(stages, holdout_start=date(2025, 3, 22)) == []

    def test_every_stage_past_the_date_is_named(self) -> None:
        stages = _stages(
            _sim("early", "2022-01-01", "2022-06-30"),
            _sim("late", "2022-01-01", "2022-12-31"),
            _wf(),
        )
        issues = pipeline_issues(stages, holdout_start=date(2022, 12, 1))
        # 'late' also reads into the first test fold, which is the range guard's finding.
        holdout_issues = [issue for issue in issues if "the holdout starts on" in issue]
        assert [issue.split("'")[1] for issue in holdout_issues] == ["late", "wf"]

    def test_without_a_date_nothing_is_checked(self) -> None:
        stages = _stages(_sim("select", "2022-01-01", "2022-12-31"), _wf())
        assert pipeline_issues(stages) == []
        assert StagedPipelineConfig(stages=stages).holdout_start is None

    def test_smoke_waives_it(self) -> None:
        stages = _stages(_sim("select", "2022-01-01", "2022-12-31"))
        assert pipeline_issues(stages, holdout_start=date(2022, 6, 1)) != []
        assert StagedPipelineConfig(stages=stages, smoke=True, holdout_start=date(2022, 6, 1)).smoke


class TestExperimentFilesAgainstTheHoldoutDate:
    """Both loaders of an experiment file check it against the one holdout date."""

    SOURCE = REPO_ROOT / "experiments" / "staged" / "ma_sweep_staged.yaml"

    @staticmethod
    def _load_both(path: Path) -> list[StagedPipelineConfig]:
        from autonomous_trading_platform.cli.commands.research import _load_experiment_from_yaml
        from autonomous_trading_platform.scheduler.cycles.run_experiment_pipeline_cycle import (
            _load_experiment_definition_from_yaml,
        )

        context = MagicMock()
        context.simulation_runner = RUNNER
        loaded = [
            _load_experiment_from_yaml(str(path), context),
            _load_experiment_definition_from_yaml(config_path=str(path), simulation_runner=RUNNER),
        ]
        configs = [definition.staged_pipeline_config for definition in loaded]
        assert all(config is not None for config in configs)
        return cast(list[StagedPipelineConfig], configs)

    def _file_reading_the_holdout(self, tmp_path: Path, *, smoke: bool) -> Path:
        raw = yaml.safe_load(self.SOURCE.read_text())
        pipeline = raw["staged_pipeline_config"]
        (monte_carlo,) = [s for s in pipeline["stages"] if s["type"] == "monte_carlo"]
        monte_carlo["end_date"] = HOLDOUT_START.isoformat()
        if smoke:
            pipeline["smoke"] = True
        path = tmp_path / "reads_the_holdout.yaml"
        path.write_text(yaml.safe_dump(raw))
        return path

    def test_the_repo_file_stops_before_the_date_and_loads(self) -> None:
        for config in self._load_both(self.SOURCE):
            assert config.holdout_start == HOLDOUT_START
            assert pipeline_issues(config.stages, holdout_start=HOLDOUT_START) == []

    def test_a_file_whose_stage_reads_the_holdout_date_is_refused_by_both(
        self, tmp_path: Path
    ) -> None:
        from autonomous_trading_platform.cli.commands.research import _load_experiment_from_yaml
        from autonomous_trading_platform.scheduler.cycles.run_experiment_pipeline_cycle import (
            _load_experiment_definition_from_yaml,
        )

        path = self._file_reading_the_holdout(tmp_path, smoke=False)
        expected = f"'elite_monte_carlo' reads up to {HOLDOUT_START}; the holdout starts on"
        context = MagicMock()
        context.simulation_runner = RUNNER
        with pytest.raises(PipelineConfigError, match=expected):
            _load_experiment_from_yaml(str(path), context)
        with pytest.raises(PipelineConfigError, match=expected):
            _load_experiment_definition_from_yaml(config_path=str(path), simulation_runner=RUNNER)

    def test_a_smoke_file_is_not_checked(self, tmp_path: Path) -> None:
        path = self._file_reading_the_holdout(tmp_path, smoke=True)
        assert [config.smoke for config in self._load_both(path)] == [True, True]
