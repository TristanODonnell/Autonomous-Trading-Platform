"""
Bench review in platform replay (portfolio rotation step 3).

Runs weekly (scheduled job `bench`) and right after a research tick, so new
research output is admitted or retired promptly. Does nothing unless
operator_settings.bench_management_enabled is on.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.bench_resimulation_service import (
    BenchResimulationService,
    BenchWindow,
    ResimOutcome,
)
from autonomous_trading_platform.application.services.bench_review_service import (
    BenchReviewService,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.runtime.platform_replay import (
    BenchReplayResult,
    PlatformReplayContext,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.storage.sor.models.dataset_versions import DatasetVersions
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

logger = get_logger(__name__)

# Below this many calendar days of data a re-sim says little; skip the review.
_MIN_WINDOW_CALENDAR_DAYS = 15


def run_bench_review_at_timestamp(
    *,
    session: Session,
    timestamp: datetime,
    replay_context: PlatformReplayContext,
    dataset_version_id: str | None = None,
) -> BenchReplayResult:
    base = dict(domain="bench", timestamp=timestamp, run_id=str(replay_context.run_id))
    if replay_context.dry_run:
        return BenchReplayResult(**base, status="dry_run", summary={"dry_run": True})

    settings = OperatorSettingsRepository(session).get_or_create_default()
    if not settings.bench_management_enabled:
        return BenchReplayResult(
            **base, status="skipped", summary={"reason": "bench_management_disabled"}
        )

    try:
        window = resolve_bench_window(
            session=session,
            as_of=timestamp.date(),
            trading_days=int(settings.bench_resim_window_days or 63),
            replay_symbols=list(replay_context.symbols),
            dataset_version_id=dataset_version_id,
        )
    except Exception as exc:
        return BenchReplayResult(**base, status="failed", errors=[f"bench_window: {exc}"])
    if window is None:
        return BenchReplayResult(
            **base, status="skipped", summary={"reason": "insufficient_data_for_window"}
        )

    try:
        from autonomous_trading_platform.research.simulation.contexts.build_simulation_context import (
            build_simulation_context,
        )

        simulation_context = build_simulation_context(
            session=session, universe_size=len(window.symbols)
        )
        bench_service = BenchReviewService(
            session,
            resimulation=BenchResimulationService(session, simulation_context.simulation_runner),
        )
        market_returns = market_daily_returns(
            session=session, window=window, simulation_runner=simulation_context.simulation_runner
        )
        review = bench_service.review(window=window, now=timestamp, market_returns=market_returns)
        session.flush()
    except Exception as exc:
        logger.exception("bench_review.replay_failed", extra={"timestamp": timestamp.isoformat()})
        session.rollback()
        return BenchReplayResult(**base, status="failed", errors=[f"bench_review: {exc}"])

    # Portfolio review (rotation step 4) right after, on the same re-sims. The bench
    # review is committed first (its retirements already commit through governance)
    # so a failed portfolio review rolls back only itself.
    session.commit()
    errors: list[str] = []
    portfolio_summary: dict[str, Any] | None = None
    try:
        portfolio_summary = _run_portfolio_review(
            session=session,
            timestamp=timestamp,
            window=window,
            bench_review_id=review.review_id,
            outcomes=bench_service.last_outcomes,
            simulation_runner=simulation_context.simulation_runner,
            market_returns=market_returns,
        )
        session.flush()
    except Exception as exc:
        logger.exception(
            "portfolio_review.replay_failed", extra={"timestamp": timestamp.isoformat()}
        )
        session.rollback()
        errors.append(f"portfolio_review: {exc}")

    skipped = [e.strategy_id for e in review.evaluations if e.decision.value == "skipped"]
    return BenchReplayResult(
        **base,
        status="ok",
        review_id=review.review_id,
        reviewed=len(review.evaluations),
        admitted=review.admitted,
        retired=review.retired,
        bench_size=len(review.bench),
        summary={
            "review_id": review.review_id,
            "window": [window.start_date.isoformat(), window.end_date.isoformat()],
            "reviewed": len(review.evaluations),
            "groups": review.group_count,
            "admitted": review.admitted,
            "retired": {
                e.strategy_id: e.reason for e in review.evaluations if e.decision.value == "retire"
            },
            "bench_size": len(review.bench),
            "skipped": skipped,
            "correlation_basis": "market_excess" if market_returns is not None else "raw",
            "portfolio_review": portfolio_summary,
        },
        errors=errors,
        warnings=[f"re-sim skipped for {len(skipped)} strategies"] if skipped else [],
    )


def _run_portfolio_review(
    *,
    session: Session,
    timestamp: datetime,
    window: BenchWindow,
    bench_review_id: str,
    outcomes: dict[str, ResimOutcome],
    simulation_runner: Any,
    market_returns: pd.Series | None = None,
) -> dict[str, Any] | None:
    """Run the portfolio review when its mode is not off; returns a replay summary."""
    from autonomous_trading_platform.application.services.portfolio_review_service import (
        PortfolioReviewService,
        review_mode,
    )
    from autonomous_trading_platform.application.services.portfolio_scorecard_service import (
        PortfolioScorecardService,
    )
    from autonomous_trading_platform.contracts.governance.portfolio_review import (
        PortfolioReviewMode,
    )

    if review_mode(session) == PortfolioReviewMode.OFF:
        return None

    def regime_label(_now: datetime) -> str | None:
        return current_regime_label(simulation_runner=simulation_runner, window=window)

    result = PortfolioReviewService(
        session, scorecards=PortfolioScorecardService(session, regime_label_fn=regime_label)
    ).run(
        now=timestamp,
        resim_outcomes=outcomes,
        bench_review_id=bench_review_id,
        window_start=window.start_date,
        window_end=window.end_date,
        market_returns=market_returns,
    )
    if result is None:
        return None
    return {
        "review_id": result.review_id,
        "mode": result.mode.value,
        "swap_eligible": result.swap_eligible,
        "regime": result.scorecards[0].regime_label if result.scorecards else None,
        "ranking": [
            {"strategy_id": c.strategy_id, "tier": c.tier, "score": _num(c.score)}
            for c in result.scorecards
        ],
        "decisions": [
            {
                "type": d.decision_type.value,
                "strategy_id": d.strategy_id,
                "counterpart_id": d.counterpart_id,
                "streak": d.streak,
                "applied": d.applied,
                "reason": d.reason,
            }
            for d in result.decisions
        ],
    }


def market_daily_returns(
    *, session: Session, window: BenchWindow, simulation_runner: Any
) -> pd.Series | None:
    """Daily returns of the market proxy (SPY) over the window, from the re-sim dataset.

    None when the dataset has no bars for it; correlations then use raw returns.
    """
    from autonomous_trading_platform.application.services.platform_replay.rotation_hooks import (
        DEFAULT_BENCHMARK,
        load_daily_closes,
    )

    closes = load_daily_closes(
        session=session,
        dataset_version=window.dataset_version,
        symbol=DEFAULT_BENCHMARK,
        start_date=window.start_date,
        end_date=window.end_date,
        price_basis=window.price_basis,
        simulation_runner=simulation_runner,
    )
    if closes is None or len(closes) < 2:
        logger.warning("bench_review.no_market_returns", extra={"window_end": str(window.end_date)})
        return None
    return closes.pct_change().dropna()


def current_regime_label(*, simulation_runner: Any, window: BenchWindow) -> str | None:
    """Market regime at the window end ("trend/volatility"), classified on the fly.

    Recorded on scorecards only (the against-the-market lens is scored in step 5).
    None when the runner exposes no bar reader or the classifier has not warmed up.
    """
    from autonomous_trading_platform.research.pipeline.gates.regime_labels import (
        OnTheFlyRegimeLabelProvider,
    )

    provider = OnTheFlyRegimeLabelProvider.from_simulation_runner(simulation_runner)
    if provider is None:
        return None
    try:
        daily = provider.load_daily_regimes(
            dataset_version=window.dataset_version,
            price_basis=window.price_basis,
            symbols=list(window.symbols),
            start_date=window.start_date,
            end_date=window.end_date,
        )
    except Exception as exc:
        logger.warning("portfolio_review.regime_label_failed", extra={"error": str(exc)})
        return None
    labelled = daily.dropna(subset=["regime_trend"]) if not daily.empty else daily
    if labelled.empty:
        return None
    last = labelled.iloc[-1]
    parts = [str(last[c]) for c in ("regime_trend", "regime_volatility") if last.get(c)]
    return "/".join(parts)[:64] or None


def _num(value: Any) -> float | None:
    return float(value) if value is not None else None


def resolve_bench_window(
    *,
    session: Session,
    as_of: date,
    trading_days: int,
    replay_symbols: list[str],
    dataset_version_id: str | None = None,
) -> BenchWindow | None:
    """The shared re-sim window: trailing `trading_days` ending at as_of.

    Mirrors the research tick: the replay's cumulative dataset in backtests (clamped
    to its coverage start), else the latest validated adjusted/raw dataset; symbols
    from the survivorship-safe universe active at the window start.
    """
    from autonomous_trading_platform.application.services.platform_replay.research_hooks import (
        _resolve_research_universe_scope,
    )

    calendar_days = math.ceil(trading_days * 7 / 5)
    start = as_of - timedelta(days=calendar_days)

    if dataset_version_id is not None:
        dataset_version = dataset_version_id
        price_basis = PriceBasis.RAW
        row = session.get(DatasetVersions, dataset_version_id)
        if row is not None and row.date_coverage_start is not None:
            start = max(start, row.date_coverage_start)
    else:
        row = None
        for name in ("adjusted_bars", "raw_bars"):
            row = (
                session.query(DatasetVersions)
                .filter(DatasetVersions.dataset_name == name)
                .filter(DatasetVersions.validation_status == "validated")
                .order_by(DatasetVersions.created_at.desc())
                .first()
            )
            if row is not None:
                break
        if row is None:
            return None
        dataset_version = row.dataset_version_id
        price_basis = PriceBasis.ADJUSTED if row.dataset_name == "adjusted_bars" else PriceBasis.RAW

    if (as_of - start).days < _MIN_WINDOW_CALENDAR_DAYS:
        return None

    scope = _resolve_research_universe_scope(session=session, start_date=start, end_date=as_of)
    symbols = sorted(scope.member_symbols)
    if dataset_version_id is not None:
        ingested = set(replay_symbols)
        symbols = [s for s in symbols if s in ingested]
    if not symbols:
        return None
    return BenchWindow(
        dataset_version=dataset_version,
        price_basis=price_basis,
        symbols=symbols,
        start_date=start,
        end_date=as_of,
    )
