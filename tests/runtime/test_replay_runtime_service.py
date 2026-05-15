from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.runtime.replay_debug import RuntimeReplayInputs
from autonomous_trading_platform.runtime.services.replay_runtime_service import ReplayRuntimeService
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns


@dataclass(frozen=True)
class ReplayServiceTestSettings:
    app_env: str = "test"
    trading_environment: TradingEnvironment = TradingEnvironment.PAPER
    no_live_trading: bool = True
    shadow_mode_enabled: bool = False
    max_gross_exposure: float = 100000.0
    max_net_exposure: float = 50000.0
    max_symbol_exposure: float = 10000.0
    max_leverage: float = 2.0
    max_orders_per_hour: int = 20
    max_orders_per_bar: int = 1
    max_daily_notional_traded: float = 25000.0
    max_reserved_cash: float = 25000.0


def test_replay_runtime_service_records_non_debug_runtime_jobs(db_session: Session) -> None:
    inputs = RuntimeReplayInputs(
        symbols=["AAPL"],
        start_date=date(2024, 1, 2),
        end_date=date(2024, 1, 2),
        starting_cash=Decimal("10000"),
        random_seed=42,
        price_basis=PriceBasis.ADJUSTED,
        calendar_mode="historical",
        cycles=["runtime_checks"],
        reset_sim_state=True,
        cadence_minutes=390,
        max_ticks=1,
    )

    summary = ReplayRuntimeService(
        settings=ReplayServiceTestSettings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run(inputs)

    rows = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.correlation_id == summary["replay_id"])
        .all()
    )
    assert {row.job_name for row in rows} == {"runtime_replay.runtime_checks"}
