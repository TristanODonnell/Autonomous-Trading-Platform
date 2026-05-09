from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from autonomous_trading_platform.storage.sor.models.strategy_control_states import (
    StrategyControlState,
)
from autonomous_trading_platform.storage.sor.repositories.core.strategy_control_state_repository import (
    StrategyControlStateRepository,
)
from autonomous_trading_platform.storage.sor.services.unit_of_work import SorUnitOfWork


def test_missing_strategy_control_state_defaults_to_enabled(db_session: Session) -> None:
    repository = StrategyControlStateRepository(db_session)

    assert repository.get_by_strategy_id("momentum_v1") is None
    assert repository.is_enabled("momentum_v1") is True


def test_set_enabled_creates_and_updates_strategy_control_state(
    db_session: Session,
) -> None:
    repository = StrategyControlStateRepository(db_session)
    first_update = datetime(2026, 5, 8, 15, 0, tzinfo=UTC)
    second_update = datetime(2026, 5, 8, 16, 0, tzinfo=UTC)

    created = repository.set_enabled(
        strategy_id="momentum_v1",
        enabled=False,
        reason="operator pause",
        updated_by="operator-1",
        updated_at=first_update,
    )
    db_session.flush()

    assert created.strategy_id == "momentum_v1"
    assert created.enabled is False
    assert repository.is_enabled("momentum_v1") is False

    updated = repository.set_enabled(
        strategy_id="momentum_v1",
        enabled=True,
        reason="operator resume",
        updated_by="operator-2",
        updated_at=second_update,
    )
    db_session.flush()

    rows = db_session.query(StrategyControlState).all()
    assert len(rows) == 1
    assert updated.strategy_id == "momentum_v1"
    assert updated.enabled is True
    assert updated.reason == "operator resume"
    assert updated.updated_by == "operator-2"
    assert updated.updated_at == second_update


def test_unit_of_work_exposes_strategy_control_states_repository(
    db_session: Session,
) -> None:
    with SorUnitOfWork(db_session) as uow:
        uow.strategy_control_states.set_enabled(
            strategy_id="momentum_v1",
            enabled=False,
            reason="operator pause",
            updated_by="operator-1",
            updated_at=datetime(2026, 5, 8, 15, 0, tzinfo=UTC),
        )

    repository = StrategyControlStateRepository(db_session)
    assert repository.is_enabled("momentum_v1") is False
