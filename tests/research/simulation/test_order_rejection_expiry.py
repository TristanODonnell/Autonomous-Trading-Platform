"""
Tests for order rejection and expiry modeling (TASK F-02).

Covers:
- Config validation: order_rejection_probability bounds, None, expire_unfilled_limit_orders bool
- Market order rejection: probability=1.0 rejects all; no fill, no carry-forward
- Rejection disabled: existing behavior unchanged
- Limit order rejection: rejected before eligibility check; no fill, no carry-forward
- Deterministic replay: same seed → same rejection decisions, fills, carry-forward
- Different seeds produce different rejection sequences
- DAY expiry disabled: touch-rejected limits carry forward (R-07 behavior preserved)
- DAY expiry enabled: touch-rejected limits expire (no carry-forward)
- DAY expiry enabled: not-price-eligible limits still dropped (unchanged)
- DAY expiry enabled: participation-capped zero-volume limits expire
- DAY expiry enabled: partial fill remainder still carries forward
- Rejection ordering: rejection occurs before participation cap and partial fill draws
- latency_bars compatibility: 0, 1, 2
- End-of-simulation: rejected/expired orders produce no phantom fills
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from autonomous_trading_platform.research.simulation.models.fill_model import (
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulated_execution_service import (
    FillBatch,
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelService,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass
class _Intent:
    intent_id: str
    run_id: str
    symbol: str
    side: str
    qty: Decimal
    order_type: str
    limit_price: Decimal | None = None


@dataclass
class _Bar:
    symbol: str
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 100_000


@dataclass
class _Costs:
    fill_price: Decimal
    commission: Decimal


class _CostModel(SimulationCostModelService):
    def __init__(self):
        pass

    def apply_costs(self, *, side, reference_price, quantity, context=None):
        return _Costs(fill_price=Decimal(str(reference_price)), commission=Decimal("0"))


_TS = datetime(2025, 7, 1, 9, 30, tzinfo=UTC)
_SYMBOL = "AAPL"


def _svc(
    *,
    order_rejection_probability: float | None = None,
    expire_unfilled_limit_orders: bool = False,
    fill_probability_on_touch: float | None = None,
    max_volume_participation_rate: float | None = None,
    partial_fill_probability: float | None = None,
    latency_bars: int = 0,
    seed: int | None = None,
) -> SimulatedExecutionService:
    rng = random.Random(seed) if seed is not None else None
    return SimulatedExecutionService(
        simulation_cost_model_service=_CostModel(),
        fill_model_config=SimulatedFillModelConfig(
            latency_bars=latency_bars,
            order_rejection_probability=order_rejection_probability,
            expire_unfilled_limit_orders=expire_unfilled_limit_orders,
            fill_probability_on_touch=fill_probability_on_touch,
            max_volume_participation_rate=max_volume_participation_rate,
            partial_fill_probability=partial_fill_probability,
        ),
        rng=rng,
    )


def _market_intent(*, side: str = "buy", qty: Decimal = Decimal("10")) -> _Intent:
    return _Intent(
        intent_id=str(uuid4()),
        run_id=str(uuid4()),
        symbol=_SYMBOL,
        side=side,
        qty=qty,
        order_type="market",
    )


def _limit_intent(*, side: str, limit_price: Decimal, qty: Decimal = Decimal("10")) -> _Intent:
    return _Intent(
        intent_id=str(uuid4()),
        run_id=str(uuid4()),
        symbol=_SYMBOL,
        side=side,
        qty=qty,
        order_type="limit",
        limit_price=limit_price,
    )


def _bar(
    *,
    open_: Decimal = Decimal("100"),
    high: Decimal = Decimal("105"),
    low: Decimal = Decimal("95"),
    close: Decimal = Decimal("102"),
    volume: int = 100_000,
) -> _Bar:
    return _Bar(
        symbol=_SYMBOL,
        timestamp=_TS,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


def test_order_rejection_probability_none_valid():
    cfg = SimulatedFillModelConfig(order_rejection_probability=None)
    assert cfg.order_rejection_probability is None


def test_order_rejection_probability_valid_values():
    for v in (0.01, 0.50, 1.0):
        cfg = SimulatedFillModelConfig(order_rejection_probability=v)
        assert cfg.order_rejection_probability == v


def test_order_rejection_probability_zero_raises():
    with pytest.raises(ValueError, match="order_rejection_probability"):
        SimulatedFillModelConfig(order_rejection_probability=0.0)


def test_order_rejection_probability_negative_raises():
    with pytest.raises(ValueError, match="order_rejection_probability"):
        SimulatedFillModelConfig(order_rejection_probability=-0.1)


def test_order_rejection_probability_above_one_raises():
    with pytest.raises(ValueError, match="order_rejection_probability"):
        SimulatedFillModelConfig(order_rejection_probability=1.01)


def test_expire_unfilled_limit_orders_defaults_false():
    cfg = SimulatedFillModelConfig()
    assert cfg.expire_unfilled_limit_orders is False


def test_expire_unfilled_limit_orders_true_valid():
    cfg = SimulatedFillModelConfig(expire_unfilled_limit_orders=True)
    assert cfg.expire_unfilled_limit_orders is True


# ---------------------------------------------------------------------------
# Market order rejection
# ---------------------------------------------------------------------------


def test_market_order_rejected_at_probability_one():
    """order_rejection_probability=1.0 rejects every market order."""
    svc = _svc(order_rejection_probability=1.0, seed=42)
    bar = _bar()
    for _ in range(10):
        batch = svc.fill(
            order_intents=[_market_intent()],
            bars_at_timestamp={_SYMBOL: bar},
        )
        assert batch.fills == [], "Rejection probability 1.0 must reject all orders"
        assert batch.carry_forward == [], "Rejected market orders must not carry forward"


def test_market_order_rejection_no_carry_forward():
    """Rejected market orders are terminal — no fill, no carry-forward."""
    svc = _svc(order_rejection_probability=1.0, seed=7)
    batch = svc.fill(
        order_intents=[_market_intent(qty=Decimal("50"))],
        bars_at_timestamp={_SYMBOL: _bar()},
    )
    assert batch.fills == []
    assert batch.carry_forward == []


def test_market_order_rejection_disabled_fills_normally():
    """With rejection disabled, market order fills as before."""
    svc = _svc(order_rejection_probability=None)
    batch = svc.fill(
        order_intents=[_market_intent()],
        bars_at_timestamp={_SYMBOL: _bar()},
    )
    assert len(batch.fills) == 1


def test_market_order_rejection_low_probability_produces_some_rejections():
    """p=0.10 → ~10% rejected over many trials."""
    bar = _bar()
    fills = 0
    for seed in range(100):
        svc = _svc(order_rejection_probability=0.10, seed=seed)
        batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: bar})
        if batch.fills:
            fills += 1
    rejections = 100 - fills
    assert rejections > 0, "Expected some rejections at p=0.10"
    assert fills > 50, "Expected most orders to fill at p=0.10"


# ---------------------------------------------------------------------------
# Limit order rejection
# ---------------------------------------------------------------------------


def test_limit_order_rejected_before_eligibility_check():
    """Rejection fires before limit eligibility; eligible price range does not save it."""
    # Bar would normally fill this limit buy (gap-open eligible)
    bar = _bar(open_=Decimal("95"), high=Decimal("103"), low=Decimal("94"), close=Decimal("101"))
    svc = _svc(order_rejection_probability=1.0, seed=0)
    batch = svc.fill(
        order_intents=[_limit_intent(side="buy", limit_price=Decimal("100"))],
        bars_at_timestamp={_SYMBOL: bar},
    )
    assert batch.fills == []
    assert batch.carry_forward == []


def test_limit_order_rejection_no_carry_forward():
    """Rejected limit orders do not carry forward regardless of expiry flag."""
    bar = _bar(open_=Decimal("95"), high=Decimal("103"), low=Decimal("94"), close=Decimal("101"))
    for expire in (False, True):
        svc = _svc(order_rejection_probability=1.0, expire_unfilled_limit_orders=expire, seed=0)
        batch = svc.fill(
            order_intents=[_limit_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        assert batch.fills == [], f"expire={expire}"
        assert batch.carry_forward == [], f"expire={expire}"


# ---------------------------------------------------------------------------
# Rejection disabled — existing behavior unchanged
# ---------------------------------------------------------------------------


def test_rejection_disabled_market_fills():
    svc = _svc(order_rejection_probability=None)
    batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: _bar()})
    assert len(batch.fills) == 1


def test_rejection_disabled_limit_intrabar_fills():
    svc = _svc(order_rejection_probability=None)
    # Intrabar touch: open=103 > limit=100, low=99 <= 100
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104"))
    batch = svc.fill(
        order_intents=[_limit_intent(side="buy", limit_price=Decimal("100"))],
        bars_at_timestamp={_SYMBOL: bar},
    )
    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


def test_deterministic_replay_market_rejection():
    """Same seed → identical rejection decisions for market orders."""
    bar = _bar()

    def run(seed: int) -> FillBatch:
        svc = _svc(order_rejection_probability=0.5, seed=seed)
        return svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: bar})

    for seed in range(20):
        a = run(seed)
        b = run(seed)
        assert len(a.fills) == len(b.fills), f"seed={seed}"
        assert len(a.carry_forward) == len(b.carry_forward)


def test_deterministic_replay_limit_rejection():
    """Same seed → identical rejection decisions for limit orders."""
    bar = _bar(open_=Decimal("95"), high=Decimal("103"), low=Decimal("94"), close=Decimal("101"))

    def run(seed: int) -> FillBatch:
        svc = _svc(order_rejection_probability=0.5, seed=seed)
        return svc.fill(
            order_intents=[_limit_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )

    for seed in range(20):
        a = run(seed)
        b = run(seed)
        assert len(a.fills) == len(b.fills)
        if a.fills:
            assert a.fills[0].price == b.fills[0].price
            assert a.fills[0].quantity == b.fills[0].quantity


def test_deterministic_replay_combined_rejection_and_touch():
    """Rejection + touch probability reproduce identically with same seed."""
    # Intrabar touch bar (open above limit)
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104"))

    def run(seed: int) -> FillBatch:
        svc = _svc(
            order_rejection_probability=0.3,
            fill_probability_on_touch=0.5,
            seed=seed,
        )
        return svc.fill(
            order_intents=[_limit_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )

    for seed in range(20):
        a = run(seed)
        b = run(seed)
        assert len(a.fills) == len(b.fills)
        assert len(a.carry_forward) == len(b.carry_forward)


def test_different_seeds_produce_different_rejection_sequences():
    """Different seeds can produce different rejection outcomes."""
    bar = _bar()
    outcomes: set[bool] = set()
    for seed in range(30):
        svc = _svc(order_rejection_probability=0.5, seed=seed)
        batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: bar})
        outcomes.add(bool(batch.fills))
    assert len(outcomes) == 2, "Expected both fill and reject outcomes across different seeds"


# ---------------------------------------------------------------------------
# DAY limit order expiry — disabled (legacy R-07 carry-forward behavior)
# ---------------------------------------------------------------------------


def test_expiry_disabled_touch_rejected_carries_forward():
    """With expire_unfilled_limit_orders=False, touch-rejected limit carries forward."""
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104"))
    rejected = False
    for seed in range(200):
        svc = _svc(fill_probability_on_touch=0.5, expire_unfilled_limit_orders=False, seed=seed)
        intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("10"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            assert len(batch.carry_forward) == 1, (
                "Touch-rejected must carry forward when expiry disabled"
            )
            assert batch.carry_forward[0].qty == Decimal("10")
            rejected = True
            break
    assert rejected, "Expected at least one touch rejection in 200 seeds"


# ---------------------------------------------------------------------------
# DAY limit order expiry — enabled
# ---------------------------------------------------------------------------


def test_expiry_enabled_touch_rejected_expires():
    """With expire_unfilled_limit_orders=True, touch-rejected limit expires (no carry-forward)."""
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104"))
    expired = False
    for seed in range(200):
        svc = _svc(fill_probability_on_touch=0.5, expire_unfilled_limit_orders=True, seed=seed)
        intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("10"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            assert batch.carry_forward == [], "Expired limit must not carry forward"
            expired = True
            break
    assert expired, "Expected at least one touch rejection in 200 seeds"


def test_expiry_enabled_touch_accepted_fills_normally():
    """When touch probability accepts, expiry flag does not affect the fill."""
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104"))
    accepted = False
    for seed in range(200):
        svc = _svc(fill_probability_on_touch=0.5, expire_unfilled_limit_orders=True, seed=seed)
        intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("10"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if batch.fills:
            assert batch.fills[0].price == Decimal("100")
            assert batch.fills[0].quantity == Decimal("10")
            accepted = True
            break
    assert accepted


def test_expiry_enabled_not_price_eligible_always_dropped():
    """Not-price-eligible limits are always dropped; expiry flag makes no difference."""
    # Bar never reaches limit
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("101"), close=Decimal("104"))
    for expire in (False, True):
        svc = _svc(expire_unfilled_limit_orders=expire)
        intent = _limit_intent(side="buy", limit_price=Decimal("100"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        assert batch.fills == [], f"expire={expire}"
        assert batch.carry_forward == [], f"expire={expire}"


def test_expiry_enabled_zero_volume_expires():
    """With expiry enabled, participation-capped zero-volume limit expires instead of carrying forward."""
    bar = _bar(
        open_=Decimal("98"),
        high=Decimal("103"),
        low=Decimal("97"),
        close=Decimal("101"),
        volume=0,
    )
    svc = _svc(max_volume_participation_rate=0.10, expire_unfilled_limit_orders=True)
    intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("20"))
    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
    assert batch.fills == []
    assert batch.carry_forward == []


def test_expiry_disabled_zero_volume_carries_forward():
    """With expiry disabled, participation-capped zero-volume limit carries forward."""
    bar = _bar(
        open_=Decimal("98"),
        high=Decimal("103"),
        low=Decimal("97"),
        close=Decimal("101"),
        volume=0,
    )
    svc = _svc(max_volume_participation_rate=0.10, expire_unfilled_limit_orders=False)
    intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("20"))
    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
    assert batch.fills == []
    assert len(batch.carry_forward) == 1
    assert batch.carry_forward[0].qty == Decimal("20")


def test_expiry_enabled_partial_fill_remainder_still_carries_forward():
    """With expiry enabled, the remainder of a partial fill still carries forward."""
    # Gap-open buy + partial fill probability=1.0 → always partial
    svc = _svc(
        partial_fill_probability=1.0,
        expire_unfilled_limit_orders=True,
        seed=42,
    )
    intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("100"))
    bar = _bar(open_=Decimal("95"), high=Decimal("103"), low=Decimal("94"), close=Decimal("101"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity < Decimal("100")
    # Remainder carries forward even with expiry enabled
    assert len(batch.carry_forward) == 1
    total = batch.fills[0].quantity + batch.carry_forward[0].qty
    assert total == Decimal("100")


def test_expiry_enabled_sell_touch_rejected_expires():
    """Expiry also applies to sell side touch rejections."""
    bar = _bar(open_=Decimal("97"), high=Decimal("101"), low=Decimal("95"), close=Decimal("98"))
    expired = False
    for seed in range(200):
        svc = _svc(fill_probability_on_touch=0.5, expire_unfilled_limit_orders=True, seed=seed)
        intent = _limit_intent(side="sell", limit_price=Decimal("100"), qty=Decimal("8"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            assert batch.carry_forward == []
            expired = True
            break
    assert expired


# ---------------------------------------------------------------------------
# Rejection ordering: before participation cap and partial fill
# ---------------------------------------------------------------------------


def test_rejection_consumes_rng_before_participation_cap():
    """Rejection draw must happen before participation cap so RNG sequence is consistent."""
    bar = _bar(volume=10)  # small volume

    # With rejection=1.0, order is rejected before cap → no fill, no carry-forward
    svc_reject = _svc(order_rejection_probability=1.0, max_volume_participation_rate=0.10, seed=0)
    batch_reject = svc_reject.fill(
        order_intents=[_market_intent(qty=Decimal("50"))], bars_at_timestamp={_SYMBOL: bar}
    )
    assert batch_reject.fills == []
    assert batch_reject.carry_forward == []


def test_rejection_consumes_rng_before_partial_fill():
    """Rejection draw happens before F-01 partial fill draw."""
    bar = _bar()
    svc = _svc(order_rejection_probability=1.0, partial_fill_probability=0.5, seed=0)
    batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: bar})
    assert batch.fills == []
    assert batch.carry_forward == []


# ---------------------------------------------------------------------------
# latency_bars compatibility
# ---------------------------------------------------------------------------


def test_rejection_with_latency_bars_0():
    """Rejection works at latency_bars=0."""
    svc = _svc(order_rejection_probability=1.0, latency_bars=0, seed=0)
    batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: _bar()})
    assert batch.fills == []
    assert batch.carry_forward == []


def test_rejection_with_latency_bars_1():
    """Rejection works at latency_bars=1 (open-price market fills)."""
    svc = _svc(order_rejection_probability=1.0, latency_bars=1, seed=0)
    batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: _bar()})
    assert batch.fills == []
    assert batch.carry_forward == []


def test_rejection_with_latency_bars_2():
    svc = _svc(order_rejection_probability=1.0, latency_bars=2, seed=0)
    batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: _bar()})
    assert batch.fills == []
    assert batch.carry_forward == []


def test_no_rejection_market_fills_with_latency_bars_1():
    """Rejection disabled; market order fills at bar.open with latency_bars=1."""
    svc = _svc(order_rejection_probability=None, latency_bars=1)
    bar = _bar(open_=Decimal("101"))
    batch = svc.fill(order_intents=[_market_intent()], bars_at_timestamp={_SYMBOL: bar})
    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("101")


def test_expiry_with_latency_bars_1():
    """Expiry flag works with latency_bars=1 (deferred limit orders)."""
    # Intrabar touch bar
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104"))
    expired = False
    for seed in range(200):
        svc = _svc(
            fill_probability_on_touch=0.5,
            expire_unfilled_limit_orders=True,
            latency_bars=1,
            seed=seed,
        )
        intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("10"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            assert batch.carry_forward == []
            expired = True
            break
    assert expired


# ---------------------------------------------------------------------------
# Multiple orders in same batch: independent rejection decisions
# ---------------------------------------------------------------------------


def test_each_order_has_independent_rejection_draw():
    """Each order in the batch gets its own rejection draw."""
    bar = _bar()
    svc = _svc(order_rejection_probability=0.5, seed=42)
    intents = [_market_intent() for _ in range(10)]
    batch = svc.fill(order_intents=intents, bars_at_timestamp={_SYMBOL: bar})
    # With p=0.5 over 10 orders, should see a mix of fills and rejections
    assert len(batch.fills) < 10, "Expected at least some rejections at p=0.5"
    assert len(batch.fills) > 0, "Expected at least some fills at p=0.5"


def test_rejection_probability_one_rejects_all_in_batch():
    """p=1.0 rejects every order in the batch."""
    bar = _bar()
    svc = _svc(order_rejection_probability=1.0, seed=0)
    intents = [_market_intent() for _ in range(5)]
    batch = svc.fill(order_intents=intents, bars_at_timestamp={_SYMBOL: bar})
    assert batch.fills == []
    assert batch.carry_forward == []


# ---------------------------------------------------------------------------
# End-of-simulation: no phantom fills
# ---------------------------------------------------------------------------


def test_rejected_orders_produce_no_phantom_fills_at_final_bar():
    """Rejected orders on the last bar must not appear in any fill or carry-forward."""
    bar = _bar()
    svc = _svc(order_rejection_probability=1.0, seed=0)
    batch = svc.fill(
        order_intents=[_market_intent(qty=Decimal("100"))], bars_at_timestamp={_SYMBOL: bar}
    )
    assert batch.fills == []
    assert batch.carry_forward == []


def test_expired_limit_orders_produce_no_phantom_fills_at_final_bar():
    """Expired limit orders on the last bar leave no carry-forward phantom."""
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104"))
    expired = False
    for seed in range(200):
        svc = _svc(fill_probability_on_touch=0.5, expire_unfilled_limit_orders=True, seed=seed)
        intent = _limit_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("10"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            assert batch.carry_forward == []
            expired = True
            break
    assert expired
