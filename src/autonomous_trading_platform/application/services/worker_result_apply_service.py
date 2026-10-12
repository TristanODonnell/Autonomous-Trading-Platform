"""Turn an accepted worker result into what Box A's weekly review consumes (plan 3.2).

The worker posts bench re-sims as ``BenchResimsPayload``; the portfolio review wants
``dict[str, ResimOutcome]`` plus the window and market returns. This service does the
conversion both ways (the worker uses ``outcomes_to_payload`` to build what it posts) and
finds the latest accepted, still-fresh ``bench_resims`` submission for a review.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.bench_resimulation_service import (
    ResimOutcome,
)
from autonomous_trading_platform.contracts.governance.worker_results import (
    BenchResimsPayload,
    ResimOutcomePayload,
    WorkerResultType,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.worker_result_submissions import (
    WorkerResultSubmissionRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.storage.sor.repositories.core.worker_result_submissions_repository import (
    WorkerResultSubmissionsRepository,
)

logger = get_logger(__name__)


@dataclass(frozen=True)
class BenchResimsForReview:
    run_id: str
    dataset_as_of: date | None
    window_start: date
    window_end: date
    outcomes: dict[str, ResimOutcome]
    market_returns: pd.Series | None


class WorkerResultApplyService:
    def __init__(
        self,
        session: Session,
        *,
        submissions_repo: WorkerResultSubmissionsRepository | None = None,
        settings_repo: OperatorSettingsRepository | None = None,
    ) -> None:
        self._submissions = submissions_repo or WorkerResultSubmissionsRepository(session)
        self._settings = settings_repo or OperatorSettingsRepository(session)

    def latest_bench_resims(self, *, now: datetime) -> BenchResimsForReview | None:
        """The newest accepted bench_resims result that is still fresh as of ``now``.

        Freshness is re-checked at use time: a result accepted on Friday may be too old
        for a review the following Thursday. Already-applied results are not reused.
        """
        rows = self._submissions.accepted_unapplied(WorkerResultType.BENCH_RESIMS.value)
        max_age = int(
            getattr(self._settings.get_or_create_default(), "worker_result_max_age_days", 3)
        )
        for row in rows:
            if row.dataset_as_of is not None and (now.date() - row.dataset_as_of).days > max_age:
                logger.warning(
                    "worker_result.too_old_to_apply",
                    extra={
                        "run_id": row.run_id,
                        "dataset_as_of": row.dataset_as_of.isoformat(),
                        "max_age_days": max_age,
                    },
                )
                continue
            return self._to_review_inputs(row)
        return None

    def mark_applied(self, run_id: str, *, review_id: str, now: datetime) -> None:
        self._submissions.mark_applied(run_id, applied_at=now, review_id=review_id)
        logger.info("worker_result.applied", extra={"run_id": run_id, "review_id": review_id})

    # ------------------------------------------------------------- conversions

    @staticmethod
    def _to_review_inputs(row: WorkerResultSubmissionRow) -> BenchResimsForReview:
        payload = BenchResimsPayload.model_validate(row.payload or {})
        return BenchResimsForReview(
            run_id=row.run_id,
            dataset_as_of=row.dataset_as_of,
            window_start=payload.window_start,
            window_end=payload.window_end,
            outcomes=payload_to_outcomes(payload),
            market_returns=_series(payload.market_returns),
        )


def payload_to_outcomes(payload: BenchResimsPayload) -> dict[str, ResimOutcome]:
    return {o.strategy_id: _to_outcome(o) for o in payload.outcomes}


def outcomes_to_payload(
    outcomes: dict[str, ResimOutcome],
    *,
    window_start: date,
    window_end: date,
    dataset_version: str | None = None,
    market_returns: pd.Series | None = None,
) -> BenchResimsPayload:
    """Worker side: the wire form of a re-sim run, ready to post."""
    return BenchResimsPayload(
        window_start=window_start,
        window_end=window_end,
        dataset_version=dataset_version,
        outcomes=[_from_outcome(o) for o in outcomes.values()],
        market_returns=_mapping(market_returns),
    )


def _to_outcome(o: ResimOutcomePayload) -> ResimOutcome:
    return ResimOutcome(
        strategy_id=o.strategy_id,
        strategy_type=o.strategy_type,
        run_id=UUID(o.run_id) if o.run_id else None,
        trade_count=o.trade_count,
        total_return=o.total_return,
        sharpe_ratio=o.sharpe_ratio,
        max_drawdown=o.max_drawdown,
        win_rate=o.win_rate,
        score=Decimal(str(o.score)) if o.score is not None else None,
        daily_turnover=o.daily_turnover,
        daily_returns=_series_or_empty(o.daily_returns),
        error=o.error,
    )


def _from_outcome(o: ResimOutcome) -> ResimOutcomePayload:
    return ResimOutcomePayload(
        strategy_id=o.strategy_id,
        strategy_type=o.strategy_type,
        run_id=str(o.run_id) if o.run_id else None,
        trade_count=o.trade_count,
        total_return=o.total_return,
        sharpe_ratio=o.sharpe_ratio,
        max_drawdown=o.max_drawdown,
        win_rate=o.win_rate,
        score=o.score,
        daily_turnover=o.daily_turnover,
        daily_returns=_mapping(o.daily_returns),
        error=o.error,
    )


def _series_or_empty(values: dict[str, float]) -> pd.Series:
    series = _series(values)
    return series if series is not None else pd.Series(dtype=float)


def _series(values: dict[str, float]) -> pd.Series | None:
    if not values:
        return None
    index = pd.to_datetime(list(values.keys()))
    return pd.Series(list(values.values()), index=index, dtype=float).sort_index()


def _mapping(series: pd.Series | None) -> dict[str, float]:
    if series is None or len(series) == 0:
        return {}
    out: dict[str, float] = {}
    for idx, value in series.items():
        key: Any = idx
        if hasattr(key, "date"):
            key = key.date()
        out[key.isoformat() if hasattr(key, "isoformat") else str(key)] = float(value)
    return out
