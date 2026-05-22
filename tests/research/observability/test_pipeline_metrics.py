"""Tests for research pipeline stage observability instrumentation."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.research.pipeline.pipeline_runner import PipelineRunner
from autonomous_trading_platform.research.pipeline.stages.base_stage import BaseStage, StageResult
from autonomous_trading_platform.strategy.configs.strategy_config import StrategyConfig


def _config(strategy_id: str = "s1") -> StrategyConfig:
    return StrategyConfig(
        strategy_id=strategy_id,
        type="moving_average_crossover",
        parameters={"short_window": 5, "long_window": 20},
    )


class _PassAllStage(BaseStage):
    """Stage that passes all survivors through unchanged."""

    def __init__(self, name: str = "test_stage") -> None:
        self._name = name

    @property
    def stage_name(self) -> str:
        return self._name

    def run(
        self, survivors, experiment_id, dataset_version, random_seed, price_basis, initial_cash
    ):
        filter_outputs = [MagicMock() for _ in survivors]
        return StageResult(
            stage_name=self._name,
            filter_outputs=filter_outputs,
            survivors=list(survivors),
        )

    @classmethod
    def from_dict(cls, raw, simulation_runner):  # type: ignore[override]
        return cls()


class _FailAllStage(BaseStage):
    """Stage that filters out all survivors."""

    @property
    def stage_name(self) -> str:
        return "fail_all"

    def run(
        self, survivors, experiment_id, dataset_version, random_seed, price_basis, initial_cash
    ):
        filter_outputs = [MagicMock() for _ in survivors]
        return StageResult(
            stage_name="fail_all",
            filter_outputs=filter_outputs,
            survivors=[],
        )

    @classmethod
    def from_dict(cls, raw, simulation_runner):  # type: ignore[override]
        return cls()


_RUN_KWARGS = dict(
    initial_configs=[_config("a"), _config("b")],
    experiment_id="exp-obs-1",
    dataset_version="v1",
    random_seed=42,
    price_basis=PriceBasis.RAW,
    initial_cash=100_000.0,
)


class TestPipelineStageMetrics:
    def test_stage_lifecycle_logs_started_and_completed(self, caplog):
        runner = PipelineRunner(stages=[_PassAllStage()])
        log_name = "autonomous_trading_platform.research.pipeline.pipeline_runner"
        with caplog.at_level(logging.INFO, logger=log_name):
            runner.run(**_RUN_KWARGS)
        messages = [r.message for r in caplog.records]
        assert "step_started" in messages
        assert "step_completed" in messages

    def test_stage_duration_in_completed_log(self, caplog):
        runner = PipelineRunner(stages=[_PassAllStage()])
        log_name = "autonomous_trading_platform.research.pipeline.pipeline_runner"
        with caplog.at_level(logging.INFO, logger=log_name):
            runner.run(**_RUN_KWARGS)
        completed = next(r for r in caplog.records if r.message == "step_completed")
        duration = completed.__dict__.get("duration_seconds")
        assert duration is not None
        assert duration >= 0.0

    def test_survivors_entered_recorded_with_stage_name(self):
        runner = PipelineRunner(stages=[_PassAllStage("my_stage")])
        with patch(
            "autonomous_trading_platform.research.pipeline.pipeline_runner.research_stage_survivors_entered"
        ) as mock_entered:
            runner.run(**_RUN_KWARGS)
        mock_entered.record.assert_called_once()
        labels = mock_entered.record.call_args.args[1]
        assert labels["stage_name"] == "my_stage"

    def test_survivors_passed_equals_initial_when_stage_passes_all(self):
        runner = PipelineRunner(stages=[_PassAllStage()])
        with patch(
            "autonomous_trading_platform.research.pipeline.pipeline_runner.research_stage_survivors_passed"
        ) as mock_passed:
            runner.run(**_RUN_KWARGS)
        count = mock_passed.record.call_args.args[0]
        assert count == len(_RUN_KWARGS["initial_configs"])

    def test_survivors_passed_zero_when_stage_fails_all(self):
        runner = PipelineRunner(stages=[_FailAllStage()])
        with patch(
            "autonomous_trading_platform.research.pipeline.pipeline_runner.research_stage_survivors_passed"
        ) as mock_passed:
            runner.run(**_RUN_KWARGS)
        count = mock_passed.record.call_args.args[0]
        assert count == 0

    def test_structured_log_emitted_on_stage_start(self, caplog):
        runner = PipelineRunner(stages=[_PassAllStage()])
        with caplog.at_level(
            logging.INFO, logger="autonomous_trading_platform.research.pipeline.pipeline_runner"
        ):
            runner.run(**_RUN_KWARGS)
        messages = [r.message for r in caplog.records]
        assert "pipeline_started" in messages

    def test_structured_log_emitted_on_stage_complete(self, caplog):
        runner = PipelineRunner(stages=[_PassAllStage()])
        with caplog.at_level(
            logging.INFO, logger="autonomous_trading_platform.research.pipeline.pipeline_runner"
        ):
            runner.run(**_RUN_KWARGS)
        messages = [r.message for r in caplog.records]
        assert "step_completed" in messages

    def test_structured_log_emitted_on_pipeline_complete(self, caplog):
        runner = PipelineRunner(stages=[_PassAllStage()])
        with caplog.at_level(
            logging.INFO, logger="autonomous_trading_platform.research.pipeline.pipeline_runner"
        ):
            runner.run(**_RUN_KWARGS)
        messages = [r.message for r in caplog.records]
        assert "pipeline_completed" in messages

    def test_early_stop_warning_logged_when_no_survivors(self, caplog):
        runner = PipelineRunner(stages=[_FailAllStage(), _PassAllStage("should_not_run")])
        with caplog.at_level(
            logging.WARNING, logger="autonomous_trading_platform.research.pipeline.pipeline_runner"
        ):
            runner.run(**_RUN_KWARGS)
        messages = [r.message for r in caplog.records]
        assert "pipeline_stage_skipped_no_survivors" in messages

    def test_stage_failure_emits_step_failed_log(self, caplog):
        class _BoomStage(BaseStage):
            @property
            def stage_name(self) -> str:
                return "boom"

            def run(self, *args, **kwargs):
                raise RuntimeError("test explosion")

            @classmethod
            def from_dict(cls, raw, simulation_runner):
                return cls()

        runner = PipelineRunner(stages=[_BoomStage()])
        log_name = "autonomous_trading_platform.research.pipeline.pipeline_runner"
        with caplog.at_level(logging.ERROR, logger=log_name), pytest.raises(RuntimeError):
            runner.run(**_RUN_KWARGS)
        messages = [r.message for r in caplog.records]
        assert "step_failed" in messages

    def test_span_created_for_stage(self):
        runner = PipelineRunner(stages=[_PassAllStage()])
        with patch(
            "autonomous_trading_platform.research.pipeline.pipeline_runner.start_span"
        ) as mock_span:
            mock_ctx = MagicMock()
            mock_ctx.__enter__ = MagicMock(return_value=MagicMock())
            mock_ctx.__exit__ = MagicMock(return_value=False)
            mock_span.return_value = mock_ctx
            runner.run(**_RUN_KWARGS)

        mock_span.assert_called_once()
        span_name = mock_span.call_args.args[0]
        assert span_name == "experiment_pipeline.stage"
        kwargs = mock_span.call_args.kwargs
        assert kwargs["stage_name"] == "test_stage"
        assert kwargs["experiment_id"] == "exp-obs-1"
