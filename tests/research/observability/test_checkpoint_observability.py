"""Tests for research checkpoint state transition observability."""

from __future__ import annotations

import logging
from unittest.mock import patch

from autonomous_trading_platform.research.checkpoints.research_checkpoint import (
    ResearchCheckpointIdentity,
    ResearchCheckpointStatus,
    ResearchTaskType,
)
from autonomous_trading_platform.research.checkpoints.research_checkpoint_service import (
    ResearchCheckpointService,
)


def _identity(
    experiment_id: str = "exp-1",
    stage_name: str = "cheap",
    strategy_id: str = "s1",
    task_type: ResearchTaskType = ResearchTaskType.SIMULATION,
) -> ResearchCheckpointIdentity:
    return ResearchCheckpointIdentity(
        experiment_id=experiment_id,
        stage_name=stage_name,
        strategy_id=strategy_id,
        task_type=task_type,
    )


class TestCheckpointObservability:
    def test_mark_running_emits_transition_log(self, caplog):
        svc = ResearchCheckpointService()
        log_name = "autonomous_trading_platform.research.checkpoints.research_checkpoint_service"
        with caplog.at_level(logging.INFO, logger=log_name):
            svc.mark_running(_identity())
        messages = [r.message for r in caplog.records]
        assert "checkpoint_state_transition" in messages

    def test_mark_completed_emits_transition_log(self, caplog):
        svc = ResearchCheckpointService()
        ident = _identity()
        log_name = "autonomous_trading_platform.research.checkpoints.research_checkpoint_service"
        with caplog.at_level(logging.INFO, logger=log_name):
            svc.mark_running(ident)
            svc.mark_completed(ident)
        transitions = [r for r in caplog.records if r.message == "checkpoint_state_transition"]
        assert len(transitions) == 2

    def test_mark_failed_emits_transition_log(self, caplog):
        svc = ResearchCheckpointService()
        ident = _identity()
        log_name = "autonomous_trading_platform.research.checkpoints.research_checkpoint_service"
        with caplog.at_level(logging.INFO, logger=log_name):
            svc.mark_running(ident)
            svc.mark_failed(ident, error_message="test error")
        transitions = [r for r in caplog.records if r.message == "checkpoint_state_transition"]
        assert len(transitions) == 2

    def test_transition_log_contains_experiment_id(self, caplog):
        svc = ResearchCheckpointService()
        log_name = "autonomous_trading_platform.research.checkpoints.research_checkpoint_service"
        with caplog.at_level(logging.INFO, logger=log_name):
            svc.mark_running(_identity(experiment_id="exp-obs-42"))
        record = next(r for r in caplog.records if r.message == "checkpoint_state_transition")
        assert record.__dict__.get("experiment_id") == "exp-obs-42"

    def test_transition_log_contains_strategy_id(self, caplog):
        svc = ResearchCheckpointService()
        log_name = "autonomous_trading_platform.research.checkpoints.research_checkpoint_service"
        with caplog.at_level(logging.INFO, logger=log_name):
            svc.mark_running(_identity(strategy_id="my-strategy"))
        record = next(r for r in caplog.records if r.message == "checkpoint_state_transition")
        assert record.__dict__.get("strategy_id") == "my-strategy"

    def test_transition_counter_incremented_on_running(self):
        svc = ResearchCheckpointService()
        with patch(
            "autonomous_trading_platform.research.checkpoints.research_checkpoint_service.research_checkpoint_transitions"
        ) as mock_counter:
            svc.mark_running(_identity())
        mock_counter.add.assert_called_once()
        args = mock_counter.add.call_args.args
        assert args[0] == 1
        labels = args[1]
        assert labels["to_status"] == ResearchCheckpointStatus.RUNNING.value

    def test_transition_counter_incremented_on_completed(self):
        svc = ResearchCheckpointService()
        ident = _identity()
        with patch(
            "autonomous_trading_platform.research.checkpoints.research_checkpoint_service.research_checkpoint_transitions"
        ) as mock_counter:
            svc.mark_running(ident)
            svc.mark_completed(ident)
        assert mock_counter.add.call_count == 2
        last_labels = mock_counter.add.call_args_list[-1].args[1]
        assert last_labels["to_status"] == ResearchCheckpointStatus.COMPLETED.value

    def test_checkpoint_duration_recorded_on_completed(self):
        svc = ResearchCheckpointService()
        ident = _identity()
        svc.mark_running(ident)
        with patch(
            "autonomous_trading_platform.research.checkpoints.research_checkpoint_service.research_checkpoint_duration"
        ) as mock_hist:
            svc.mark_completed(ident)
        mock_hist.record.assert_called_once()
        duration = mock_hist.record.call_args.args[0]
        assert duration >= 0.0

    def test_checkpoint_duration_recorded_on_failed(self):
        svc = ResearchCheckpointService()
        ident = _identity()
        svc.mark_running(ident)
        with patch(
            "autonomous_trading_platform.research.checkpoints.research_checkpoint_service.research_checkpoint_duration"
        ) as mock_hist:
            svc.mark_failed(ident, error_message="bang")
        mock_hist.record.assert_called_once()

    def test_checkpoint_duration_recorded_on_cache_hit(self):
        svc = ResearchCheckpointService()
        ident = _identity()
        with patch(
            "autonomous_trading_platform.research.checkpoints.research_checkpoint_service.research_checkpoint_duration"
        ) as mock_hist:
            svc.mark_cache_hit(ident, cache_key="ck1", cached_run_id="r1")
        mock_hist.record.assert_called_once()

    def test_task_type_label_correct_on_transition(self):
        svc = ResearchCheckpointService()
        ident = _identity(task_type=ResearchTaskType.WALK_FORWARD_FOLD)
        with patch(
            "autonomous_trading_platform.research.checkpoints.research_checkpoint_service.research_checkpoint_transitions"
        ) as mock_counter:
            svc.mark_running(ident)
        labels = mock_counter.add.call_args.args[1]
        assert labels["task_type"] == ResearchTaskType.WALK_FORWARD_FOLD.value
