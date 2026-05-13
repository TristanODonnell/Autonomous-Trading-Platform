from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.common.enums import OrderSource, PriceBasis
from autonomous_trading_platform.runtime.clock import FakeTradingClock, HistoricalMarketCalendar
from autonomous_trading_platform.runtime.replay_debug import (
    RuntimeReplayDebugRunner,
    RuntimeReplayInputs,
    format_text_summary,
    load_settings_snapshot,
)
from autonomous_trading_platform.storage.sor.models.cash_snapshots import CashSnapshot
from autonomous_trading_platform.storage.sor.models.operator_settings import OperatorSettingsRow
from autonomous_trading_platform.storage.sor.models.runtime_job_runs import RuntimeJobRuns
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


@dataclass(frozen=True)
class ReplayTestSettings:
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


def _settings() -> ReplayTestSettings:
    return ReplayTestSettings()


def _inputs(
    *,
    cycles: list[str] | None = None,
    reset: bool = False,
    max_ticks: int | None = 2,
) -> RuntimeReplayInputs:
    return RuntimeReplayInputs(
        symbols=["AAPL", "MSFT"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 5),
        starting_cash=Decimal("10000"),
        random_seed=42,
        price_basis=PriceBasis.ADJUSTED,
        calendar_mode="historical",
        cycles=cycles or ["trading", "portfolio_snapshot"],
        reset_sim_state=reset,
        cadence_minutes=390,
        max_ticks=max_ticks,
    )


def test_fake_clock_advances_through_historical_sessions() -> None:
    calendar = HistoricalMarketCalendar()
    ticks = list(
        calendar.scheduled_times(
            date(2024, 1, 1),
            date(2024, 1, 2),
            timedelta(minutes=390),
        )
    )
    clock = FakeTradingClock(ticks[0])

    clock.advance_to(ticks[-1])

    assert clock.now() == ticks[-1]
    assert len(ticks) == 4


def test_calendar_skips_non_trading_days() -> None:
    calendar = HistoricalMarketCalendar()

    ticks = list(
        calendar.scheduled_times(
            date(2024, 1, 6),
            date(2024, 1, 8),
            timedelta(minutes=390),
        )
    )

    assert {tick.date() for tick in ticks} == {date(2024, 1, 8)}


def test_runner_loads_persisted_settings_instead_of_cli_business_settings(
    db_session: Session,
) -> None:
    repo = OperatorSettingsRepository(db_session)
    repo.update_current(
        {
            "max_drawdown_limit": Decimal("0.0500"),
            "rebalance_frequency": "daily",
        },
        updated_by="test",
    )

    summary = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["runtime_checks"], max_ticks=1),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()

    loaded = summary["loaded_settings"]["operator_settings"]
    assert loaded["max_drawdown_limit"] == 0.05
    assert loaded["rebalance_frequency"] == "daily"


def test_reset_sim_state_resets_simulated_rows_but_preserves_settings(
    db_session: Session,
) -> None:
    repo = OperatorSettingsRepository(db_session)
    repo.update_current({"max_drawdown_limit": Decimal("0.0700")}, updated_by="test")
    db_session.add(
        CashSnapshot(
            snapshot_id=uuid.uuid4(),
            run_id=uuid.uuid4(),
            timestamp=datetime(2024, 1, 1, tzinfo=UTC),
            currency="USD",
            cash=Decimal("1"),
            buying_power=Decimal("1"),
            reserved_cash=Decimal("0"),
            equity=Decimal("1"),
            source=OrderSource.SIMULATION,
            capital_bucket=Decimal("1"),
        )
    )
    db_session.flush()

    RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["runtime_checks"], reset=True, max_ticks=0),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()

    settings = db_session.get(OperatorSettingsRow, "default")
    snapshots = db_session.query(CashSnapshot).all()
    assert settings is not None
    assert float(settings.max_drawdown_limit) == 0.07
    assert all(snapshot.cash != Decimal("1") for snapshot in snapshots)


def test_selected_cycles_run_in_requested_order(db_session: Session) -> None:
    calls: list[str] = []

    def _handler(name: str):
        def _run(_ctx):
            calls.append(name)
            return {"name": name}

        return _run

    RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["features", "trading", "rebalance"], max_ticks=1),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
        cycle_handlers={
            "features": _handler("features"),
            "trading": _handler("trading"),
            "rebalance": _handler("rebalance"),
        },
    ).run()

    assert calls == ["features", "trading", "rebalance"]


def test_runtime_job_records_are_created(db_session: Session) -> None:
    summary = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["runtime_checks"], max_ticks=2),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()

    jobs = (
        db_session.query(RuntimeJobRuns)
        .filter(RuntimeJobRuns.correlation_id == summary["replay_id"])
        .all()
    )
    assert len(jobs) == 2
    assert {job.job_name for job in jobs} == {"runtime_replay_debug.runtime_checks"}
    assert all(job.trigger_type == "manual_cli" for job in jobs)


def test_summary_includes_loaded_settings_and_result_counts(db_session: Session) -> None:
    summary = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["trading", "rebalance"], max_ticks=1),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()
    rendered = format_text_summary(summary)

    assert "Loaded settings:" in rendered
    assert "max_drawdown_limit" in rendered
    assert summary["trading"]["signals_generated"] == 2
    assert summary["execution"]["cycle_invocations"] == {"trading": 1, "rebalance": 1}


def test_changing_persisted_setting_changes_loaded_snapshot(db_session: Session) -> None:
    repo = OperatorSettingsRepository(db_session)
    repo.update_current({"max_drawdown_limit": Decimal("0.1000")}, updated_by="test")
    first = load_settings_snapshot(session=db_session, settings=_settings())

    repo.update_current({"max_drawdown_limit": Decimal("0.0500")}, updated_by="test")
    second = load_settings_snapshot(session=db_session, settings=_settings())

    assert first["operator_settings"]["max_drawdown_limit"] == 0.1
    assert second["operator_settings"]["max_drawdown_limit"] == 0.05


def test_tighter_max_drawdown_causes_more_risk_blocks(db_session: Session) -> None:
    repo = OperatorSettingsRepository(db_session)
    repo.update_current({"max_drawdown_limit": Decimal("0.5000")}, updated_by="test")
    loose = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["trading"], max_ticks=5),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()

    repo.update_current({"max_drawdown_limit": Decimal("0.0050")}, updated_by="test")
    tight = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["trading"], reset=True, max_ticks=5),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()

    assert tight["trading"]["orders_blocked_by_risk"] > loose["trading"]["orders_blocked_by_risk"]


def test_dispatched_ticks_are_all_unique(db_session: Session) -> None:
    """Every tick the handler is called with a distinct clock timestamp."""
    dispatched: list[datetime] = []

    def record_tick(ctx):
        dispatched.append(ctx.clock.now())
        return None

    summary = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["runtime_checks"], max_ticks=4),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
        cycle_handlers={"runtime_checks": record_tick},
    ).run()

    assert summary["execution"]["ticks_dispatched"] == 4
    assert len(dispatched) == 4
    assert len(set(dispatched)) == 4


def test_ticks_dispatched_counter_matches_handler_invocation_count(
    db_session: Session,
) -> None:
    """ticks_dispatched in the summary equals the number of times the handler was called."""
    call_count = 0

    def counting_handler(ctx):
        nonlocal call_count
        call_count += 1
        return None

    summary = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["runtime_checks"], max_ticks=6),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
        cycle_handlers={"runtime_checks": counting_handler},
    ).run()

    assert summary["execution"]["ticks_dispatched"] == call_count
    assert call_count == 6


def test_output_json_is_comparison_friendly(db_session: Session, tmp_path: Path) -> None:
    output = tmp_path / "latest.json"

    summary = RuntimeReplayDebugRunner(
        inputs=_inputs(cycles=["runtime_checks"], max_ticks=1),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()

    assert summary["inputs"]["symbols"] == ["AAPL", "MSFT"]

    RuntimeReplayDebugRunner(
        inputs=RuntimeReplayInputs(
            **{
                **_inputs(cycles=["runtime_checks"], max_ticks=1).__dict__,
                "output_json": output,
            }
        ),
        settings=_settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()

    assert output.exists()
