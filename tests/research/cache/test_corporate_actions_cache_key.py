"""The simulation cache key carries the corporate actions given to a run (plan 5d-E)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.common.enums import CorporateActionType
from autonomous_trading_platform.contracts.market.corporate_action import CorporateAction
from autonomous_trading_platform.research.cache.cache_identity import SimulationCacheKey
from autonomous_trading_platform.research.cache.cache_key_builder import (
    _hash_corporate_actions,
)
from autonomous_trading_platform.research.cache.simulation_result_cache import _entry_to_key


def _action(action_id: str, ratio: str, ex_date: date = date(2024, 6, 10)) -> CorporateAction:
    return CorporateAction(
        action_id=action_id,
        symbol="NVDA",
        action_type=CorporateActionType.SPLIT_FORWARD,
        effective_date=ex_date,
        split_ratio=Decimal(ratio),
        cash_amount=None,
        currency="USD",
        new_symbol="",
        source="alpaca",
        ingested_at=datetime(2026, 10, 2, tzinfo=UTC),
    )


def _key(**overrides: Any) -> SimulationCacheKey:
    fields: dict[str, Any] = dict(
        config_hash="c",
        dataset_version="v",
        universe_version="v1",
        price_basis="raw",
        symbols_hash="s",
        start_date="2024-06-03",
        end_date="2024-06-14",
        random_seed=1,
        stage_name="default",
        window_role="default",
        fill_policy="close",
        latency_bars=0,
        cost_model_type="volume_share",
        slippage_config_hash="h",
        commission_per_share="0",
        regime_dataset_version="",
        feature_versions_hash="",
    )
    fields.update(overrides)
    return SimulationCacheKey(**fields)


def test_hash_is_empty_without_actions_and_order_independent() -> None:
    assert _hash_corporate_actions(None) == ""
    assert _hash_corporate_actions([]) == ""
    a, b = _action("x", "10"), _action("y", "4", date(2024, 1, 15))
    assert _hash_corporate_actions([a, b]) == _hash_corporate_actions([b, a])
    assert len(_hash_corporate_actions([a])) == 16


def test_hash_ignores_the_provider_id_but_not_the_economics() -> None:
    assert _hash_corporate_actions([_action("id-1", "10")]) == _hash_corporate_actions(
        [_action("id-2", "10")]
    )
    assert _hash_corporate_actions([_action("x", "10")]) != _hash_corporate_actions(
        [_action("x", "4")]
    )


def test_key_id_changes_with_the_actions_hash_and_round_trips() -> None:
    plain = _key()
    with_actions = _key(corporate_actions_hash=_hash_corporate_actions([_action("x", "10")]))
    assert plain.key_id != with_actions.key_id
    assert plain.to_dict()["corporate_actions_hash"] == ""
    assert _entry_to_key(with_actions.to_dict()) == with_actions
    # entries persisted before 5d-E have no hash and still load
    legacy = {k: v for k, v in plain.to_dict().items() if k != "corporate_actions_hash"}
    assert _entry_to_key(legacy) == plain
