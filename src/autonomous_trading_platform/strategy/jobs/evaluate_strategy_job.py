from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.strategy.services.strategy_bar_readiness_service import (
    StrategyBarReadinessService,
)
from autonomous_trading_platform.strategy.services.strategy_evaluation_service import (
    StrategyEvaluationService,
)


class SignalWriterProtocol:
    def save_many(self, signals: list[Signal]) -> None:
        raise NotImplementedError


class StrategyCheckpointWriterProtocol:
    def mark_evaluated(self, bar_timestamp: datetime) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class EvaluateStrategyJobResult:
    evaluated: bool
    reason: str | None
    signals_emitted: int
    target_bar_timestamp: datetime | None
    signals: list[Signal]


class EvaluateStrategyJob:
    def __init__(
        self,
        readiness_service: StrategyBarReadinessService,
        evaluation_service: StrategyEvaluationService,
        signal_writer: SignalWriterProtocol,
        checkpoint_writer: StrategyCheckpointWriterProtocol,
    ) -> None:
        self.readiness_service = readiness_service
        self.evaluation_service = evaluation_service
        self.signal_writer = signal_writer
        self.checkpoint_writer = checkpoint_writer

    def run(self, now: datetime) -> EvaluateStrategyJobResult:
        readiness = self.readiness_service.get_next_ready_bar(now)
        run_id = uuid4()

        if readiness.target_bar_timestamp is None:
            return EvaluateStrategyJobResult(
                evaluated=False,
                reason=readiness.reason,
                signals_emitted=0,
                target_bar_timestamp=None,
                signals=[],
            )
        result = self.evaluation_service.evaluate(
            bar_timestamp=readiness.target_bar_timestamp,
            run_id=run_id,
            evaluation_timestamp=now,
        )

        if result.signals:
            self.signal_writer.save_many(result.signals)

        self.checkpoint_writer.mark_evaluated(readiness.target_bar_timestamp)

        return EvaluateStrategyJobResult(
            evaluated=True,
            reason=None,
            signals_emitted=len(result.signals),
            target_bar_timestamp=readiness.target_bar_timestamp,
            signals=result.signals,
        )
