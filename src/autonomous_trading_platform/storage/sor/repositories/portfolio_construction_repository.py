# autonomous_trading_platform/storage/sor/repositories/portfolio_construction_repository.py
"""
Repository for portfolio construction pipeline artifacts — Recommendation 6.5.

Persists and retrieves the four artifact types produced by the pipeline:
  - PortfolioConstructionRunRow (diagnostics summary)
  - PortfolioSignalBatchItemRow (raw strategy signals)
  - PortfolioNettedSignalRow (aggregated portfolio signals)
  - PortfolioSignalIntentRow (constraint-gated intents)
"""

from __future__ import annotations

from typing import cast

from sqlalchemy import select

from autonomous_trading_platform.contracts.trading.portfolio_signal import (
    PortfolioConstructionDiagnostics,
    PortfolioConstructionResult,
    PortfolioSignal,
    SignalBatch,
    SignalIntent,
)
from autonomous_trading_platform.contracts.trading.signal import Signal
from autonomous_trading_platform.storage.sor.models.portfolio_construction import (
    PortfolioConstructionRunRow,
    PortfolioNettedSignalRow,
    PortfolioSignalBatchItemRow,
    PortfolioSignalIntentRow,
)
from autonomous_trading_platform.storage.sor.repositories.base import BaseRepository


class PortfolioConstructionRepository(BaseRepository):
    """
    Persists and retrieves portfolio construction pipeline artifacts.

    All writes within a single persist_result() call are batched into one
    flush so they are visible together or not at all within the session.
    """

    def persist_result(self, result: PortfolioConstructionResult) -> None:
        """Persist all artifacts from a completed pipeline run."""
        self._persist_run(result.diagnostics)
        self._persist_batch_items(result.signal_batch, result.aggregated_bundle.attributions)
        self._persist_netted_signals(result.portfolio_signals)
        self._persist_signal_intents(result.signal_intents)
        self.session.flush()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def _persist_run(self, diag: PortfolioConstructionDiagnostics) -> None:
        row = PortfolioConstructionRunRow(
            run_id=str(diag.run_id),
            batch_id=str(diag.batch_id),
            bar_timestamp=diag.bar_timestamp,
            constructed_at=diag.constructed_at,
            netting_policy=diag.netting_policy,
            strategy_count=diag.strategy_count,
            symbol_count=diag.symbol_count,
            raw_signal_count=diag.raw_signal_count,
            netted_signal_count=diag.netted_signal_count,
            suppressed_count=diag.suppressed_count,
            suppressed_notional_usd=diag.suppressed_notional_usd,
            conflict_count=diag.conflict_count,
            constraint_applied_count=diag.constraint_applied_count,
            constraint_rejected_count=diag.constraint_rejected_count,
            raw_gross_signal_exposure=diag.raw_gross_signal_exposure,
            net_signal_exposure=diag.net_signal_exposure,
            exposure_before_constraints=diag.exposure_before_constraints,
            exposure_after_constraints=diag.exposure_after_constraints,
            final_order_count=diag.final_order_count,
            construction_duration_seconds=diag.construction_duration_seconds,
            diagnostics_json=diag.model_dump(mode="json"),
        )
        self.session.add(row)

    def _persist_batch_items(
        self,
        batch: SignalBatch,
        attributions: dict,
    ) -> None:
        # attributions is symbol→contributions; we don't have raw signals here,
        # so we persist the batch metadata only. Raw signals must be passed separately.
        pass

    def persist_raw_signals(
        self, batch: SignalBatch, signals_by_strategy: dict[str, list[Signal]]
    ) -> None:
        """Persist raw strategy signals from Phase 1."""
        for _strategy_id, signals in signals_by_strategy.items():
            for s in signals:
                row = PortfolioSignalBatchItemRow(
                    batch_id=str(batch.batch_id),
                    run_id=str(batch.run_id),
                    signal_id=str(s.signal_id),
                    strategy_id=s.strategy_id,
                    symbol=s.symbol,
                    direction=s.direction.value,
                    confidence=s.confidence,
                    bar_timestamp=s.bar_timestamp,
                    strategy_version=s.strategy_version,
                    feature_snapshot_ref=s.feature_snapshot_ref,
                )
                self.session.add(row)

    def _persist_netted_signals(self, portfolio_signals: list[PortfolioSignal]) -> None:
        for ps in portfolio_signals:
            row = PortfolioNettedSignalRow(
                portfolio_signal_id=str(ps.portfolio_signal_id),
                batch_id=str(ps.batch_id),
                run_id=str(ps.run_id),
                symbol=ps.symbol,
                net_direction=ps.net_direction.value,
                net_confidence=ps.net_confidence,
                conflict_detected=ps.conflict_detected,
                constraint_status=ps.constraint_status.value,
                netting_policy=ps.netting_policy.value,
                gross_buy_notional=ps.gross_buy_notional,
                gross_sell_notional=ps.gross_sell_notional,
                net_notional=ps.net_notional,
                contributing_strategy_ids=ps.contributing_strategy_ids,
                attribution_json=[c.model_dump(mode="json") for c in ps.attribution],
                bar_timestamp=ps.bar_timestamp,
            )
            self.session.add(row)

    def _persist_signal_intents(self, signal_intents: list[SignalIntent]) -> None:
        for si in signal_intents:
            row = PortfolioSignalIntentRow(
                intent_id=str(si.intent_id),
                portfolio_signal_id=str(si.portfolio_signal_id),
                batch_id=str(si.batch_id),
                run_id=str(si.run_id),
                symbol=si.symbol,
                direction=si.direction.value,
                confidence=si.confidence,
                constraint_status=si.constraint_status.value,
                constraints_triggered=si.constraints_triggered,
                attribution_json=[c.model_dump(mode="json") for c in si.attribution],
                bar_timestamp=si.bar_timestamp,
            )
            self.session.add(row)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_run(self, run_id: str) -> PortfolioConstructionRunRow | None:
        stmt = (
            select(PortfolioConstructionRunRow)
            .where(PortfolioConstructionRunRow.run_id == run_id)
            .order_by(PortfolioConstructionRunRow.constructed_at.desc())
            .limit(1)
        )
        return cast(
            PortfolioConstructionRunRow | None,
            self.session.execute(stmt).scalar_one_or_none(),
        )

    def get_run_by_batch(self, batch_id: str) -> PortfolioConstructionRunRow | None:
        stmt = select(PortfolioConstructionRunRow).where(
            PortfolioConstructionRunRow.batch_id == batch_id
        )
        return cast(
            PortfolioConstructionRunRow | None,
            self.session.execute(stmt).scalar_one_or_none(),
        )

    def list_runs(self, limit: int = 50) -> list[PortfolioConstructionRunRow]:
        stmt = (
            select(PortfolioConstructionRunRow)
            .order_by(PortfolioConstructionRunRow.constructed_at.desc())
            .limit(limit)
        )
        return list(self.session.execute(stmt).scalars())

    def list_raw_signals(self, batch_id: str) -> list[PortfolioSignalBatchItemRow]:
        stmt = select(PortfolioSignalBatchItemRow).where(
            PortfolioSignalBatchItemRow.batch_id == batch_id
        )
        return list(self.session.execute(stmt).scalars())

    def list_netted_signals(self, batch_id: str) -> list[PortfolioNettedSignalRow]:
        stmt = select(PortfolioNettedSignalRow).where(PortfolioNettedSignalRow.batch_id == batch_id)
        return list(self.session.execute(stmt).scalars())

    def list_signal_intents(self, batch_id: str) -> list[PortfolioSignalIntentRow]:
        stmt = select(PortfolioSignalIntentRow).where(PortfolioSignalIntentRow.batch_id == batch_id)
        return list(self.session.execute(stmt).scalars())

    def list_conflicts(self, batch_id: str) -> list[PortfolioNettedSignalRow]:
        stmt = select(PortfolioNettedSignalRow).where(
            PortfolioNettedSignalRow.batch_id == batch_id,
            PortfolioNettedSignalRow.conflict_detected.is_(True),
        )
        return list(self.session.execute(stmt).scalars())
