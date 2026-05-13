"""
Tests proving that risk_tolerance, target_portfolio_volatility, and
max_strategy_drawdown are wired to the replay runtime and change actual
order-sizing / blocking behavior — not just persisted to the DB.

Each test runs two back-to-back replays with the same deterministic seed
and compares a metric that the setting controls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.enums import TradingEnvironment
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.runtime.replay_debug import (
    RuntimeReplayDebugRunner,
    RuntimeReplayInputs,
)
from autonomous_trading_platform.storage.sor.models.risk_snapshots import RiskSnapshot
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)


@dataclass(frozen=True)
class _Settings:
    app_env: str = "test"
    trading_environment: TradingEnvironment = TradingEnvironment.PAPER
    no_live_trading: bool = True
    shadow_mode_enabled: bool = False
    max_gross_exposure: float = 1_000_000.0
    max_net_exposure: float = 1_000_000.0
    max_symbol_exposure: float = 500_000.0
    max_leverage: float = 4.0
    max_orders_per_hour: int = 100
    max_orders_per_bar: int = 10
    max_daily_notional_traded: float = 1_000_000.0
    max_reserved_cash: float = 0.0


def _inputs(*, max_ticks: int = 5, reset: bool = False) -> RuntimeReplayInputs:
    return RuntimeReplayInputs(
        symbols=["AAPL", "MSFT"],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        starting_cash=Decimal("10000"),
        random_seed=42,
        price_basis=PriceBasis.ADJUSTED,
        calendar_mode="historical",
        cycles=["trading"],
        reset_sim_state=reset,
        cadence_minutes=390,
        max_ticks=max_ticks,
    )


def _run(db_session: Session, *, reset: bool = False, max_ticks: int = 5) -> dict:
    return RuntimeReplayDebugRunner(
        inputs=_inputs(max_ticks=max_ticks, reset=reset),
        settings=_Settings(),
        session_factory=lambda: db_session,
        close_session=False,
    ).run()


# ---------------------------------------------------------------------------
# Layer 1 — risk_tolerance
# ---------------------------------------------------------------------------


def test_risk_tolerance_low_leaves_more_cash_than_high(db_session: Session) -> None:
    """low (0.5x cap) deploys less capital per fill than high (1.5x cap)."""
    repo = OperatorSettingsRepository(db_session)

    repo.update_current(
        {"risk_tolerance": "low", "max_drawdown_limit": Decimal("0.99")},
        updated_by="test",
    )
    low = _run(db_session, reset=True, max_ticks=3)

    repo.update_current(
        {"risk_tolerance": "high", "max_drawdown_limit": Decimal("0.99")},
        updated_by="test",
    )
    high = _run(db_session, reset=True, max_ticks=3)

    assert low["portfolio"]["cash"] > high["portfolio"]["cash"], (
        f"Expected low-tolerance cash ({low['portfolio']['cash']:.2f}) "
        f"> high-tolerance cash ({high['portfolio']['cash']:.2f})"
    )


def test_risk_tolerance_medium_is_between_low_and_high(db_session: Session) -> None:
    """medium (1.0x) produces cash between low (0.5x) and high (1.5x)."""
    repo = OperatorSettingsRepository(db_session)

    repo.update_current(
        {"risk_tolerance": "low", "max_drawdown_limit": Decimal("0.99")},
        updated_by="test",
    )
    low = _run(db_session, reset=True, max_ticks=3)

    repo.update_current({"risk_tolerance": "medium"}, updated_by="test")
    medium = _run(db_session, reset=True, max_ticks=3)

    repo.update_current({"risk_tolerance": "high"}, updated_by="test")
    high = _run(db_session, reset=True, max_ticks=3)

    assert low["portfolio"]["cash"] > medium["portfolio"]["cash"] > high["portfolio"]["cash"], (
        f"Expected low > medium > high cash: "
        f"{low['portfolio']['cash']:.2f} / {medium['portfolio']['cash']:.2f} / {high['portfolio']['cash']:.2f}"
    )


def test_risk_tolerance_appears_in_trading_cycle_output(db_session: Session) -> None:
    """_run_trading now returns risk_tolerance_mult so callers can inspect it."""
    repo = OperatorSettingsRepository(db_session)
    repo.update_current({"risk_tolerance": "low"}, updated_by="test")

    # Use a custom cycle handler that captures the output
    captured: list[dict] = []

    from autonomous_trading_platform.runtime.replay_debug import (
        DEFAULT_CYCLE_HANDLERS,
        ReplayCycleContext,
    )

    def _capturing_trading(ctx: ReplayCycleContext) -> dict:
        result = DEFAULT_CYCLE_HANDLERS["trading"](ctx)
        if result:
            captured.append(result)
        return result or {}

    RuntimeReplayDebugRunner(
        inputs=_inputs(max_ticks=1),
        settings=_Settings(),
        session_factory=lambda: db_session,
        close_session=False,
        cycle_handlers={"trading": _capturing_trading},
    ).run()

    assert captured, "No trading cycle output captured"
    assert captured[0].get("risk_tolerance_mult") == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Layer 2 — target_portfolio_volatility
# ---------------------------------------------------------------------------


def test_low_vol_target_scales_down_notional_vs_high_target(db_session: Session) -> None:
    """
    With a very low volatility target the vol scalar shrinks notional once
    equity history is long enough.  More cash remains than with a high target.
    """
    repo = OperatorSettingsRepository(db_session)

    # Very tight target: scalar = target/realized_vol will be small once vol builds up
    repo.update_current(
        {
            "target_portfolio_volatility": Decimal("0.01"),
            "risk_tolerance": "medium",
            "max_drawdown_limit": Decimal("0.99"),
        },
        updated_by="test",
    )
    tight_vol = _run(db_session, reset=True, max_ticks=5)

    # Generous target: target/realized_vol >= 1 → scalar clamped to 1.0 (no scaling)
    repo.update_current(
        {"target_portfolio_volatility": Decimal("0.99")},
        updated_by="test",
    )
    loose_vol = _run(db_session, reset=True, max_ticks=5)

    assert tight_vol["portfolio"]["cash"] > loose_vol["portfolio"]["cash"], (
        f"Expected tight-vol cash ({tight_vol['portfolio']['cash']:.2f}) "
        f"> loose-vol cash ({loose_vol['portfolio']['cash']:.2f})"
    )


def test_vol_scalar_is_one_when_history_is_too_short(db_session: Session) -> None:
    """With only 1 tick there is not enough history; scalar defaults to 1.0."""
    repo = OperatorSettingsRepository(db_session)
    repo.update_current(
        {"target_portfolio_volatility": Decimal("0.01"), "max_drawdown_limit": Decimal("0.99")},
        updated_by="test",
    )

    captured: list[dict] = []
    from autonomous_trading_platform.runtime.replay_debug import (
        DEFAULT_CYCLE_HANDLERS,
        ReplayCycleContext,
    )

    def _capturing_trading(ctx: ReplayCycleContext) -> dict:
        result = DEFAULT_CYCLE_HANDLERS["trading"](ctx)
        if result:
            captured.append(result)
        return result or {}

    RuntimeReplayDebugRunner(
        inputs=_inputs(max_ticks=1),
        settings=_Settings(),
        session_factory=lambda: db_session,
        close_session=False,
        cycle_handlers={"trading": _capturing_trading},
    ).run()

    # With only 1 equity point (< _VOL_HISTORY_MIN_BARS=3), scalar must be 1.0
    assert captured[0].get("vol_scalar") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Layer 3 — max_strategy_drawdown
# ---------------------------------------------------------------------------


def test_tight_strategy_drawdown_blocks_orders_before_portfolio_limit(
    db_session: Session,
) -> None:
    """
    A tight per-strategy limit blocks orders even when the portfolio-level
    limit is intentionally set so high it will never fire.
    """
    repo = OperatorSettingsRepository(db_session)
    repo.update_current(
        {
            "max_drawdown_limit": Decimal("0.99"),  # portfolio limit: won't trigger
            "max_strategy_drawdown": Decimal("0.001"),  # strategy limit: 0.1%, will trigger fast
            "risk_tolerance": "medium",
        },
        updated_by="test",
    )

    result = _run(db_session, reset=True, max_ticks=5)

    assert result["trading"]["orders_blocked_by_risk"] > 0, (
        "Expected orders blocked by per-strategy drawdown; none were."
    )


def test_loose_strategy_drawdown_blocks_fewer_orders_than_tight(db_session: Session) -> None:
    """Tightening max_strategy_drawdown increases the blocked order count."""
    repo = OperatorSettingsRepository(db_session)

    repo.update_current(
        {
            "max_drawdown_limit": Decimal("0.99"),
            "max_strategy_drawdown": Decimal("0.001"),  # very tight
        },
        updated_by="test",
    )
    tight = _run(db_session, reset=True, max_ticks=5)

    repo.update_current(
        {"max_strategy_drawdown": Decimal("0.99")},  # very loose
        updated_by="test",
    )
    loose = _run(db_session, reset=True, max_ticks=5)

    assert (
        tight["trading"]["orders_blocked_by_risk"] > loose["trading"]["orders_blocked_by_risk"]
    ), (
        f"Tight ({tight['trading']['orders_blocked_by_risk']}) should block "
        f"more than loose ({loose['trading']['orders_blocked_by_risk']})"
    )


def test_strategy_drawdown_breach_recorded_in_risk_snapshot(db_session: Session) -> None:
    """When a per-strategy limit fires, the breach is recorded in the risk snapshot."""
    repo = OperatorSettingsRepository(db_session)
    repo.update_current(
        {
            "max_drawdown_limit": Decimal("0.99"),
            "max_strategy_drawdown": Decimal("0.001"),
        },
        updated_by="test",
    )

    summary = _run(db_session, reset=True, max_ticks=5)
    replay_id = summary["replay_id"]

    import uuid as _uuid

    snapshots = (
        db_session.query(RiskSnapshot).filter(RiskSnapshot.run_id == _uuid.UUID(replay_id)).all()
    )

    breach_snapshots = [s for s in snapshots if s.limits and s.limits.get("per_strategy_breaches")]
    assert breach_snapshots, (
        "Expected at least one RiskSnapshot with per_strategy_breaches data; found none."
    )
    # Verify breach record has the required evidence fields
    breach_data = next(iter(breach_snapshots[0].limits["per_strategy_breaches"].values()))
    assert "drawdown" in breach_data
    assert "peak_equity" in breach_data
    assert "current_equity" in breach_data
    assert "limit" in breach_data


# ---------------------------------------------------------------------------
# Notification wiring - notify_drawdown_alerts
# ---------------------------------------------------------------------------


def test_notify_drawdown_alerts_true_emits_and_persists_drawdown_breach_alert(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = OperatorSettingsRepository(db_session)
    repo.update_current(
        {
            "notify_drawdown_alerts": True,
            "max_drawdown_limit": Decimal("0.0050"),
            "max_strategy_drawdown": Decimal("0.99"),
        },
        updated_by="test",
    )

    with caplog.at_level(logging.CRITICAL):
        summary = _run(db_session, reset=True, max_ticks=5)

    snapshots = _risk_snapshots_for_replay(db_session, summary["replay_id"])
    drawdown_alerts = _drawdown_alerts_from_snapshots(snapshots)

    assert summary["trading"]["orders_blocked_by_risk"] > 0
    assert any(snapshot.is_blocked for snapshot in snapshots)
    assert len(drawdown_alerts) == 1
    assert drawdown_alerts[0]["alert_type"] == "drawdown_breach"
    assert any(record.getMessage() == "risk_alert.drawdown_breach" for record in caplog.records)


def test_notify_drawdown_alerts_false_blocks_without_drawdown_breach_alert(
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    repo = OperatorSettingsRepository(db_session)
    repo.update_current(
        {
            "notify_drawdown_alerts": False,
            "max_drawdown_limit": Decimal("0.0050"),
            "max_strategy_drawdown": Decimal("0.99"),
        },
        updated_by="test",
    )

    with caplog.at_level(logging.CRITICAL):
        summary = _run(db_session, reset=True, max_ticks=5)

    snapshots = _risk_snapshots_for_replay(db_session, summary["replay_id"])

    assert summary["trading"]["orders_blocked_by_risk"] > 0
    assert any(snapshot.is_blocked for snapshot in snapshots)
    assert _drawdown_alerts_from_snapshots(snapshots) == []
    assert not any(record.getMessage() == "risk_alert.drawdown_breach" for record in caplog.records)


def _risk_snapshots_for_replay(
    db_session: Session,
    replay_id: str,
) -> list[RiskSnapshot]:
    import uuid as _uuid

    return cast(
        list[RiskSnapshot],
        db_session.query(RiskSnapshot).filter(RiskSnapshot.run_id == _uuid.UUID(replay_id)).all(),
    )


def _drawdown_alerts_from_snapshots(
    snapshots: list[RiskSnapshot],
) -> list[dict]:
    return [
        alert
        for snapshot in snapshots
        for alert in (snapshot.limits or {}).get("risk_alerts", [])
        if alert.get("alert_type") == "drawdown_breach"
    ]
