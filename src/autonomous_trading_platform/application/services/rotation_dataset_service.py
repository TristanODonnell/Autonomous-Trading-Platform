"""
Rotation dataset export (portfolio rotation step 5).

Run once, right after a recording backtest (the database still holds that run):

  - one full-period re-simulation per strategy that was ever in the pool, over the
    whole run on the run's own bars dataset (daily closing equity + fills), tagged
    as a bench re-sim so it never becomes approval evidence;
  - availability: seeded approved strategies from the start; candidates from their
    bench admission until their bench retirement;
  - promotability: governance's own promotion check for each candidate;
  - review dates, the market proxy's daily returns, the run's operator settings;
  - the recorded path (rotation report + review decisions) for validation.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services.quality_based_reallocation_service import (
    BENCH_RESIM_EXPERIMENT_PREFIX,
    QualityBasedReallocationService,
)
from autonomous_trading_platform.application.services.strategy_governance_service import (
    StrategyGovernanceService,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.contracts.governance.rotation_dataset import (
    RotationDataset,
    RotationFill,
    RotationStrategySeries,
)
from autonomous_trading_platform.observability.logging import get_logger
from autonomous_trading_platform.research.simulation.simulation_runner import (
    SimulationRunRequest,
)
from autonomous_trading_platform.storage.sor.models.bench_evaluations import BenchEvaluationRow
from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipTransitionRow,
)
from autonomous_trading_platform.storage.sor.models.portfolio_reviews import (
    PortfolioReviewDecisionRow,
    PortfolioReviewRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.models.strategy_governance import StrategyGovernance
from autonomous_trading_platform.storage.sor.models.strategy_sleeves import (
    ShadowSleeveLedgerRow,
    ShadowSleeveSnapshotRow,
    StrategySleeveLedgerRow,
    StrategySleeveSnapshotRow,
)
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)
from autonomous_trading_platform.strategy.configs.stored_config import stored_config_parameters

logger = get_logger(__name__)

EXPORT_EXPERIMENT_ID = f"{BENCH_RESIM_EXPERIMENT_PREFIX}rotation_export"
_PAPER = "approved_for_paper_trading"
_LIVE = "approved_for_live_trading"
_SETTINGS_FIELDS = (
    "min_active_strategies",
    "max_active_strategies",
    "max_on_deck_strategies",
    "max_bench_strategies",
    "max_total_strategy_allocation_pct",
    "per_strategy_cap",
    "min_allocation_change_pct",
    "portfolio_review_mode",
    "review_swap_margin",
    "review_swap_consecutive",
    "review_min_tenure_days",
    "review_max_swaps_per_review",
    "review_swap_interval_days",
    "review_turnover_cost_bps",
    "review_min_shadow_days",
    "review_min_shadow_trades",
    "review_score_floor",
    "review_on_deck_min_tenure_days",
)


class RotationDatasetService:
    def __init__(
        self,
        session: Session,
        simulation_runner: Any,
        *,
        initial_cash: float = 100_000.0,
        random_seed: int = 42,
    ) -> None:
        self._session = session
        self._runner = simulation_runner
        self._initial_cash = initial_cash
        self._seed = random_seed

    def export(
        self,
        *,
        start_date: date,
        end_date: date,
        starting_cash: float,
        dataset_version: str,
        symbols: list[str],
        market_closes: pd.Series | None = None,
        market_symbol: str | None = None,
        fixture_name: str | None = None,
        recorded_rotation: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> RotationDataset:
        settings = OperatorSettingsRepository(self._session).get_or_create_default()
        pool = self._pool(start_date=start_date)
        governance = StrategyGovernanceService(session=self._session)
        backtest = QualityBasedReallocationService(session=self._session)
        promoted_ids = self._promoted()

        series: list[RotationStrategySeries] = []
        for index, (sid, info) in enumerate(sorted(pool.items()), start=1):
            if progress:
                progress(f"[{index}/{len(pool)}] re-simulating {sid}")
            config = self._session.get(StrategyConfigs, sid)
            if config is None or not config.strategy_type:
                logger.warning("rotation_dataset.missing_config", extra={"strategy_id": sid})
                continue
            equity, fills = self._resimulate(
                sid,
                config,
                start_date=start_date,
                end_date=end_date,
                dataset_version=dataset_version,
                symbols=symbols,
            )
            promoted = sid in promoted_ids
            promotable = promoted
            if not promoted and not info["seeded_approved"] and info["state"] == "candidate":
                promotable, _ = governance.would_pass_promotion(sid, _PAPER)
            forward_returns, forward_book, forward_closed = self._forward_record(
                sid, end_date=end_date
            )
            score = backtest.backtest_quality_score(sid)
            series.append(
                RotationStrategySeries(
                    strategy_id=sid,
                    strategy_type=config.strategy_type,
                    seeded_approved=info["seeded_approved"],
                    promotable=promotable,
                    promoted_in_run=promoted,
                    approval_score=float(score) if score is not None else None,
                    available_from=info["available_from"],
                    retired_on=info["retired_on"],
                    equity=equity,
                    fills=fills,
                    forward_returns=forward_returns,
                    forward_book=forward_book,
                    forward_closed=forward_closed,
                )
            )

        market_returns = (
            {d: float(r) for d, r in market_closes.sort_index().pct_change().dropna().items()}
            if market_closes is not None and not market_closes.empty
            else {}
        )
        return RotationDataset(
            fixture_name=fixture_name,
            dataset_version=dataset_version,
            start_date=start_date,
            end_date=end_date,
            starting_cash=starting_cash,
            re_sim_initial_cash=self._initial_cash,
            review_dates=[
                at
                for at in self._session.scalars(
                    select(PortfolioReviewRow.reviewed_at).order_by(PortfolioReviewRow.reviewed_at)
                ).all()
                if at.date() <= end_date
            ],
            market_symbol=market_symbol,
            market_returns=market_returns,
            settings={f: _plain(getattr(settings, f, None)) for f in _SETTINGS_FIELDS},
            initial_active=self._initial_active(),
            strategies=series,
            recorded_rotation=recorded_rotation,
            recorded_decisions=[
                d
                for d in self._recorded_decisions()
                if date.fromisoformat(d["reviewed_at"][:10]) <= end_date
            ],
        )

    # ------------------------------------------------------------------ pool

    def _pool(self, *, start_date: date) -> dict[str, dict[str, Any]]:
        """Every strategy that was ever tradeable or admitted to the bench."""
        states: dict[str, str] = {}
        for row in self._session.scalars(
            select(StrategyGovernance).order_by(StrategyGovernance.updated_at.desc())
        ):
            states.setdefault(row.strategy_id, row.current_state)

        # Approved at the end and not promoted by the review during the run.
        seeded = {
            sid for sid, state in states.items() if state in (_PAPER, _LIVE)
        } - self._promoted()

        admitted: dict[str, date] = {}
        retired: dict[str, date] = {}
        for ev in self._session.scalars(
            select(BenchEvaluationRow).order_by(BenchEvaluationRow.reviewed_at)
        ):
            day = ev.reviewed_at.date()
            if ev.decision == "admit":
                admitted.setdefault(ev.strategy_id, day)
            elif ev.decision == "retire":
                retired.setdefault(ev.strategy_id, day)

        pool: dict[str, dict[str, Any]] = {}
        for sid in seeded:
            pool[sid] = {
                "seeded_approved": True,
                "state": states.get(sid, _PAPER),
                "available_from": start_date,
                "retired_on": None,
            }
        for sid, day in admitted.items():
            if sid in pool:
                continue
            pool[sid] = {
                "seeded_approved": False,
                "state": "candidate",
                "available_from": day,
                "retired_on": retired.get(sid),
            }
        return pool

    def _promoted(self) -> set[str]:
        """Strategies the recording run promoted (they started as candidates)."""
        return {
            d.strategy_id
            for d in self._session.scalars(select(PortfolioReviewDecisionRow))
            if (d.guardrails or {}).get("governance") is True and d.applied
        }

    def _initial_active(self) -> list[str]:
        rows = self._session.scalars(
            select(PortfolioMembershipTransitionRow).order_by(
                PortfolioMembershipTransitionRow.created_at
            )
        ).all()
        if not rows:
            return []
        first_day = rows[0].created_at.date()
        return sorted(
            {
                r.strategy_id
                for r in rows
                if r.created_at.date() == first_day and r.to_status == "active"
            }
        )

    def _recorded_decisions(self) -> list[dict[str, Any]]:
        return [
            {
                "reviewed_at": d.reviewed_at.isoformat(),
                "type": d.decision_type,
                "strategy_id": d.strategy_id,
                "counterpart_id": d.counterpart_id,
                "streak": d.streak,
                "applied": d.applied,
                "reason": d.reason,
            }
            for d in self._session.scalars(
                select(PortfolioReviewDecisionRow).order_by(
                    PortfolioReviewDecisionRow.reviewed_at, PortfolioReviewDecisionRow.decision_type
                )
            )
        ]

    # ------------------------------------------------------------------ forward

    def _forward_record(
        self, sid: str, *, end_date: date | None = None
    ) -> tuple[dict[date, float], dict[date, str], dict[date, tuple[int, int]]]:
        """Daily return on allocated capital (real sleeve preferred, else shadow) and
        closed-trade counts, i.e. the strategy's forward record at platform cadence."""
        returns: dict[date, float] = {}
        book: dict[date, str] = {}
        for name, model in (
            ("shadow", ShadowSleeveSnapshotRow),
            ("real", StrategySleeveSnapshotRow),
        ):
            rows = self._session.execute(
                select(model.timestamp, model.net_pnl, model.allocated_capital)
                .where(model.strategy_id == sid)
                .order_by(model.timestamp)
            ).all()
            by_day: dict[date, tuple[float, float | None]] = {}
            for ts, net_pnl, allocated in rows:
                if end_date is not None and ts.date() > end_date:
                    continue
                by_day[ts.date()] = (
                    float(net_pnl),
                    float(allocated) if allocated is not None else None,
                )
            days = sorted(by_day)
            for prev, day in zip(days, days[1:], strict=False):
                prev_pnl, prev_alloc = by_day[prev]
                if not prev_alloc or prev_alloc <= 0:
                    continue
                returns[day] = (by_day[day][0] - prev_pnl) / prev_alloc  # real overwrites shadow
                book[day] = name
        closed: dict[date, list[int]] = {}
        for model in (ShadowSleeveLedgerRow, StrategySleeveLedgerRow):
            for ts, side, realized in self._session.execute(
                select(model.timestamp, model.side, model.realized_pnl).where(
                    model.strategy_id == sid
                )
            ).all():
                if str(side).lower() != "sell" or (end_date is not None and ts.date() > end_date):
                    continue
                counts = closed.setdefault(ts.date(), [0, 0])
                counts[0] += 1
                counts[1] += 1 if float(realized) > 0 else 0
        return returns, book, {d: (c[0], c[1]) for d, c in closed.items()}

    # ------------------------------------------------------------------ re-sim

    def _resimulate(
        self,
        sid: str,
        config: StrategyConfigs,
        *,
        start_date: date,
        end_date: date,
        dataset_version: str,
        symbols: list[str],
    ) -> tuple[list[tuple[datetime, float]], list[RotationFill]]:
        try:
            result = self._runner.run(
                SimulationRunRequest(
                    strategy_id=sid,
                    strategy_config={
                        "type": config.strategy_type,
                        "strategy_id": sid,
                        "parameters": stored_config_parameters(config.config_json),
                    },
                    dataset_version=dataset_version,
                    random_seed=self._seed,
                    price_basis=PriceBasis.RAW,
                    symbols=list(symbols),
                    start_date=start_date,
                    end_date=end_date,
                    initial_cash=self._initial_cash,
                    experiment_id=EXPORT_EXPERIMENT_ID,
                    window_role="bench",
                    stage_name="rotation_export",
                )
            )
        except Exception as exc:
            logger.warning(
                "rotation_dataset.resim_failed", extra={"strategy_id": sid, "error": str(exc)}
            )
            return [], []
        return bar_equity(result.equity_curve), _fills(result.trade_logs)


def bar_equity(equity_curve: pd.DataFrame | None) -> list[tuple[datetime, float]]:
    if equity_curve is None or equity_curve.empty:
        return []
    frame = equity_curve[["timestamp", "equity"]].sort_values("timestamp")
    return [
        (pd.Timestamp(ts).to_pydatetime(), float(eq))
        for ts, eq in zip(frame["timestamp"], frame["equity"], strict=True)
    ]


def _fills(trade_logs: pd.DataFrame | None) -> list[RotationFill]:
    if trade_logs is None or trade_logs.empty:
        return []
    return [
        RotationFill(
            timestamp=pd.Timestamp(row["timestamp"]).to_pydatetime(),
            symbol=str(row["symbol"]),
            side=str(row["side"]).lower(),
            quantity=float(row["quantity"]),
            price=float(row["price"]),
            fees=float(row.get("fees") or 0.0),
        )
        for _, row in trade_logs.iterrows()
    ]


def _plain(value: Any) -> Any:
    try:
        return float(value) if hasattr(value, "as_tuple") else value
    except Exception:
        return value
