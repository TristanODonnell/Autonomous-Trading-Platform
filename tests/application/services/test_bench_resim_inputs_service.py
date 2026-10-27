"""Box A's bench re-sim inputs for the worker (plan 3.3a)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.application.services import bench_resim_inputs_service as mod
from autonomous_trading_platform.application.services.bench_resim_inputs_service import (
    BenchResimInputsService,
)
from autonomous_trading_platform.application.services.bench_resimulation_service import (
    BenchWindow,
)
from autonomous_trading_platform.contracts.common.enums import PriceBasis
from autonomous_trading_platform.storage.sor.models.portfolio_memberships import (
    PortfolioMembershipRow,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.storage.sor.repositories.core.operator_settings_repository import (
    OperatorSettingsRepository,
)

_NOW = datetime(2026, 10, 23, 1, 0, tzinfo=UTC)
_WINDOW = BenchWindow(
    dataset_version="raw_bars_20261022",
    price_basis=PriceBasis.RAW,
    symbols=["AAPL", "MSFT"],
    start_date=date(2026, 7, 27),
    end_date=date(2026, 10, 23),
)


def seed_tracked_strategy(
    session: Session,
    strategy_id: str,
    *,
    tier: str,
    strategy_type: str | None = "momentum",
    config: dict | None = None,
    with_config: bool = True,
) -> None:
    session.add(
        PortfolioMembershipRow(
            strategy_id=strategy_id,
            status=tier,
            since=_NOW,
            reason="seed",
            quality_score=None,
            updated_by="test",
            updated_at=_NOW,
        )
    )
    if with_config:
        session.add(
            StrategyConfigs(
                strategy_id=strategy_id,
                config_hash=f"hash_{strategy_id}",
                config_json=config if config is not None else {"lookback": 5},
                created_at=_NOW,
                strategy_type=strategy_type,
                metadata_json=None,
            )
        )
    session.flush()


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_resolve(**kwargs):
        calls.append(kwargs)
        return _WINDOW

    monkeypatch.setattr(mod, "resolve_bench_window", fake_resolve)
    return calls


def test_lists_reviewed_tiers_with_unwrapped_parameters_and_the_shared_window(
    db_session: Session, window: list[dict]
) -> None:
    seed_tracked_strategy(db_session, "momentum_v1", tier="active")
    seed_tracked_strategy(
        db_session,
        "macd_crossover_v1",
        tier="bench",
        strategy_type="moving_average_crossover",
        # research stores {type, parameters}; the worker gets the parameters only
        config={"type": "moving_average_crossover", "parameters": {"long_window": 30}},
    )
    seed_tracked_strategy(db_session, "mr_v1", tier="on_deck", strategy_type="mean_reversion")
    seed_tracked_strategy(db_session, "old_v1", tier="winding_down")
    seed_tracked_strategy(db_session, "gone_v1", tier="active", with_config=False)
    OperatorSettingsRepository(db_session).update_current(
        {"bench_resim_window_days": 42}, updated_by="test"
    )

    inputs = BenchResimInputsService(db_session, local_git_sha="abc1234").build(
        as_of=date(2026, 10, 23)
    )

    assert [s.strategy_id for s in inputs.strategies] == [
        "macd_crossover_v1",
        "momentum_v1",
        "mr_v1",
    ]
    by_id = {s.strategy_id: s for s in inputs.strategies}
    assert by_id["macd_crossover_v1"].tier == "bench"
    assert by_id["macd_crossover_v1"].parameters == {"long_window": 30}
    assert by_id["macd_crossover_v1"].strategy_type == "moving_average_crossover"
    assert by_id["momentum_v1"].parameters == {"lookback": 5}
    assert by_id["momentum_v1"].config_hash == "hash_momentum_v1"

    assert inputs.trading_days == 42
    assert inputs.git_sha == "abc1234"
    assert inputs.window_start == date(2026, 7, 27)
    assert inputs.window_end == date(2026, 10, 23)
    assert inputs.dataset_version == "raw_bars_20261022"
    assert inputs.price_basis == "raw"
    assert inputs.symbols == ["AAPL", "MSFT"]
    assert inputs.window_reason is None
    assert inputs.initial_cash == 100_000.0 and inputs.random_seed == 42

    [call] = window
    assert call["as_of"] == date(2026, 10, 23)
    assert call["trading_days"] == 42
    assert call["replay_symbols"] == []


def test_explicit_trading_days_override_the_setting(
    db_session: Session, window: list[dict]
) -> None:
    inputs = BenchResimInputsService(db_session, local_git_sha=None).build(
        as_of=date(2026, 10, 23), trading_days=21
    )

    assert inputs.trading_days == 21
    assert inputs.strategies == []
    assert inputs.git_sha is None
    assert window[0]["trading_days"] == 21


def test_no_window_yet_is_reported_not_raised(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod, "resolve_bench_window", lambda **kwargs: None)
    seed_tracked_strategy(db_session, "momentum_v1", tier="active")

    inputs = BenchResimInputsService(db_session, local_git_sha="x").build(as_of=date(2026, 10, 23))

    assert [s.strategy_id for s in inputs.strategies] == ["momentum_v1"]
    assert inputs.window_start is None and inputs.window_end is None
    assert inputs.dataset_version is None and inputs.symbols == []
    assert inputs.window_reason == "insufficient_data_for_window"
