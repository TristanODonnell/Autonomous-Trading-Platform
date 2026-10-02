"""The trading cycle builds each strategy with the parameters it was researched with."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy.orm import Session

from autonomous_trading_platform.config.settings import Settings
from autonomous_trading_platform.scheduler.common.trading_cycle_common import (
    _instantiate_strategy,
    _resolve_active_strategy,
)
from autonomous_trading_platform.storage.sor.models.strategy_configs import StrategyConfigs
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy
from autonomous_trading_platform.strategy.registry import get_registry
from tests.conftest import seed_strategy_governance

_T0 = datetime(2026, 9, 28, tzinfo=UTC)

# A real research-generated composite_rule config (stored wrapped).
_COMPOSITE_PARAMS: dict[str, Any] = {
    "sizing": {},
    "filters": [],
    "metadata": {"generation_template": "thresh_z_20_v1_weig"},
    "aggregator": {
        "component": "weighted_score",
        "parameters": {"buy_threshold": 0.55, "sell_threshold": -0.55},
    },
    "indicators": [{"id": "z_20", "component": "z_score", "parameters": {"window": 20}}],
    "entry_rules": [
        {
            "id": "entry",
            "inputs": {"value": {"offset": 0, "indicator_id": "z_20"}},
            "weight": 1.0,
            "component": "threshold",
            "parameters": {"buy_below": -1.5, "confidence": 0.62, "sell_above": 1.5},
        }
    ],
    "confirmations": [],
    "strategy_type": "composite_rule",
    "confidence_scoring": {"cap": 1.0, "mode": "aggregation", "floor": 0.0},
}


def _config(session: Session, strategy_id: str, strategy_type: str, config_json: dict) -> None:
    session.add(
        StrategyConfigs(
            strategy_id=strategy_id,
            config_hash=f"{strategy_id}_hash",
            config_json=config_json,
            created_at=_T0,
            strategy_type=strategy_type,
        )
    )
    session.flush()


def _wrapped(strategy_type: str, strategy_id: str, parameters: dict) -> dict:
    return {"type": strategy_type, "strategy_id": strategy_id, "parameters": parameters}


@pytest.mark.parametrize(
    ("strategy_type", "parameters", "expected"),
    [
        ("momentum", {"lookback": 5, "buy_above": 0.1, "sell_below": -0.1}, {"buy_above": 0.1}),
        (
            "mean_reversion",
            {"window": 5, "buy_below_z": -2.0, "sell_above_z": 0.5},
            {"window": 5, "sell_above_z": 0.5},
        ),
        (
            "factor_based",
            {"momentum_lookback": 1, "volatility_window": 100, "buy_score_threshold": 0.1},
            {"momentum_lookback": 1, "volatility_window": 100, "buy_score_threshold": 0.1},
        ),
    ],
)
def test_wrapped_research_config_uses_researched_parameters(
    db_session: Session, strategy_type: str, parameters: dict, expected: dict
) -> None:
    sid = f"{strategy_type}__abc"
    _config(db_session, sid, strategy_type, _wrapped(strategy_type, sid, parameters))

    strategy, _ = _instantiate_strategy(db_session, sid)

    assert not isinstance(strategy, StubStrategy)
    for name, value in expected.items():
        assert getattr(strategy, name) == value


def test_warmup_comes_from_the_researched_parameters(db_session: Session) -> None:
    params = {"window": 100, "buy_below_z": -2.0, "sell_above_z": 2.0}
    _config(
        db_session, "mr__long", "mean_reversion", _wrapped("mean_reversion", "mr__long", params)
    )

    _, warmup = _instantiate_strategy(db_session, "mr__long")

    assert warmup >= 100


def test_composite_rule_config_builds_a_real_strategy(db_session: Session) -> None:
    sid = "composite_rule__abc"
    _config(db_session, sid, "composite_rule", _wrapped("composite_rule", sid, _COMPOSITE_PARAMS))

    strategy, warmup = _instantiate_strategy(db_session, sid)

    assert type(strategy).__name__ == "CompositeRuleStrategy"
    assert warmup >= 20


def test_bare_parameter_config_still_works(db_session: Session) -> None:
    _config(db_session, "momentum_v1", "momentum", {})
    _config(db_session, "momentum_fast", "momentum", {"buy_above": 0.2})

    default, _ = _instantiate_strategy(db_session, "momentum_v1")
    tuned, _ = _instantiate_strategy(db_session, "momentum_fast")

    assert not isinstance(default, StubStrategy)
    assert vars(tuned)["buy_above"] == 0.2


def test_invalid_parameters_fall_back_to_a_stub_with_the_real_id(db_session: Session) -> None:
    sid = "momentum__bad"
    _config(db_session, sid, "momentum", _wrapped("momentum", sid, {"lookback": "not a number"}))

    strategy, _ = _instantiate_strategy(db_session, sid)

    assert isinstance(strategy, StubStrategy)
    assert strategy.strategy_id == sid


def test_warmup_is_the_shared_context_lookback(db_session: Session) -> None:
    """Research re-sims hand a strategy the same number of bars (rotation step 5c)."""
    params = {"short_window": 10, "long_window": 30}
    _config(
        db_session,
        "macd__x",
        "moving_average_crossover",
        _wrapped("moving_average_crossover", "macd__x", params),
    )

    _, warmup = _instantiate_strategy(db_session, "macd__x")

    expected = get_registry().get_definition("moving_average_crossover")
    assert warmup == expected.context_lookback_bars(params) == 31


def test_single_strategy_cycle_uses_researched_parameters(db_session: Session) -> None:
    """Portfolio mode off: the legacy path merged the wrapped config over the defaults,
    so a research candidate traded with default parameters there."""
    sid = "momentum__legacy"
    params = {"lookback": 12, "buy_above": 0.3, "sell_below": -0.3}
    _config(db_session, sid, "momentum", _wrapped("momentum", sid, params))
    seed_strategy_governance(db_session, strategy_id=sid)

    strategy, strategy_id, _, warmup = _resolve_active_strategy(
        session=db_session, settings=Settings()
    )

    assert strategy_id == sid
    assert not isinstance(strategy, StubStrategy)
    assert (vars(strategy)["lookback"], vars(strategy)["buy_above"]) == (12, 0.3)
    assert warmup == 13
