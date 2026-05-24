"""
Tests for limit order fill realism improvements (TASK R-07).

Covers:
- Gap-open buy: bar.open <= limit_price → fill at bar.open, not limit_price
- Gap-open sell: bar.open >= limit_price → fill at bar.open, not limit_price
- Intrabar touch with fill_probability_on_touch=None → legacy deterministic behavior
- Intrabar touch with fill_probability_on_touch set → probabilistic gate
- Exact-touch (bar.low == limit_price, bar.high == limit_price) → probability-gated
- Touch rejection → carry forward full quantity (not silently dropped)
- Touch acceptance → fills normally and carries forward remainder
- fill_probability_on_touch config validation (bounds, None)
- Deterministic replay: same seed + same inputs → identical outcomes
- Integration: latency_bars compatibility (0, 1, 2)
- Integration: max_volume_participation_rate applied after eligibility
- Integration: partial_fill_probability applied after eligibility
- Integration: end-of-simulation carry-forward expiry unaffected
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
    SimulatedExecutionService,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelService,
)

# ---------------------------------------------------------------------------
# Shared test helpers
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


_TS = datetime(2025, 6, 1, 9, 30, tzinfo=UTC)
_SYMBOL = "AAPL"


def _service(
    *,
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
            fill_probability_on_touch=fill_probability_on_touch,
            max_volume_participation_rate=max_volume_participation_rate,
            partial_fill_probability=partial_fill_probability,
        ),
        rng=rng,
    )


def _intent(*, side: str, limit_price: Decimal, qty: Decimal = Decimal("10")) -> _Intent:
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
    open_: Decimal,
    high: Decimal,
    low: Decimal,
    close: Decimal,
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


def test_fill_probability_on_touch_none_is_valid():
    cfg = SimulatedFillModelConfig(fill_probability_on_touch=None)
    assert cfg.fill_probability_on_touch is None


def test_fill_probability_on_touch_valid_values():
    for v in (0.01, 0.50, 1.0):
        cfg = SimulatedFillModelConfig(fill_probability_on_touch=v)
        assert cfg.fill_probability_on_touch == v


def test_fill_probability_on_touch_zero_raises():
    with pytest.raises(ValueError, match="fill_probability_on_touch"):
        SimulatedFillModelConfig(fill_probability_on_touch=0.0)


def test_fill_probability_on_touch_negative_raises():
    with pytest.raises(ValueError, match="fill_probability_on_touch"):
        SimulatedFillModelConfig(fill_probability_on_touch=-0.1)


def test_fill_probability_on_touch_above_one_raises():
    with pytest.raises(ValueError, match="fill_probability_on_touch"):
        SimulatedFillModelConfig(fill_probability_on_touch=1.01)


# ---------------------------------------------------------------------------
# Gap-open buy
# ---------------------------------------------------------------------------


def test_gap_open_buy_fills_at_bar_open():
    """bar.open (98) < limit_price (100) → fill at 98, not 100."""
    svc = _service()
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("98"), high=Decimal("103"), low=Decimal("97"), close=Decimal("101"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("98")


def test_gap_open_buy_not_at_limit_price():
    svc = _service()
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("95"), high=Decimal("99"), low=Decimal("94"), close=Decimal("97"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price != Decimal("100")
    assert batch.fills[0].price == Decimal("95")


def test_gap_open_buy_exact_open_equals_limit():
    """bar.open == limit_price → treated as gap-open, fills at bar.open = limit_price."""
    svc = _service()
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("100"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")


def test_gap_open_buy_bypasses_touch_probability():
    """Gap-open fills are never gated by fill_probability_on_touch."""
    # probability=0.0 is invalid, so we use an RNG that always returns >= prob
    # by setting probability very close to 1 but using a seeded RNG that would reject
    # an intrabar touch — gap-open should still fill.
    # bar opens below limit → gap-open
    bar = _bar(open_=Decimal("98"), high=Decimal("103"), low=Decimal("97"), close=Decimal("101"))

    # Run many times; since gap-open bypasses the gate, all should fill
    results = []
    for _ in range(20):
        svc2 = _service(fill_probability_on_touch=0.01, seed=_)
        b = svc2.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        results.append(len(b.fills))

    # Gap-open always fills regardless of RNG outcome
    assert all(r == 1 for r in results)


# ---------------------------------------------------------------------------
# Gap-open sell
# ---------------------------------------------------------------------------


def test_gap_open_sell_fills_at_bar_open():
    """bar.open (103) > limit_price (100) → fill at 103, not 100."""
    svc = _service()
    intent = _intent(side="sell", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("103"), high=Decimal("105"), low=Decimal("101"), close=Decimal("102"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("103")


def test_gap_open_sell_not_at_limit_price():
    svc = _service()
    intent = _intent(side="sell", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("108"), high=Decimal("110"), low=Decimal("106"), close=Decimal("107"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("108")


def test_gap_open_sell_exact_open_equals_limit():
    """bar.open == limit_price → gap-open, fills at bar.open = limit_price."""
    svc = _service()
    intent = _intent(side="sell", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("100"), high=Decimal("103"), low=Decimal("98"), close=Decimal("101"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")


def test_gap_open_sell_bypasses_touch_probability():
    """Gap-open sell fills are never gated by fill_probability_on_touch."""
    bar = _bar(open_=Decimal("105"), high=Decimal("108"), low=Decimal("103"), close=Decimal("106"))
    results = []
    for s in range(20):
        svc = _service(fill_probability_on_touch=0.01, seed=s)
        b = svc.fill(
            order_intents=[_intent(side="sell", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        results.append(len(b.fills))
    assert all(r == 1 for r in results)


# ---------------------------------------------------------------------------
# Intrabar touch — probability disabled (legacy behavior)
# ---------------------------------------------------------------------------


def test_intrabar_buy_touch_no_probability_always_fills():
    """With fill_probability_on_touch=None, intrabar touch fills deterministically."""
    svc = _service(fill_probability_on_touch=None)
    intent = _intent(side="buy", limit_price=Decimal("100"))
    # bar.open (102) > limit (100), bar.low (99) <= limit → intrabar touch
    bar = _bar(open_=Decimal("102"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")


def test_intrabar_sell_touch_no_probability_always_fills():
    """With fill_probability_on_touch=None, intrabar touch fills deterministically."""
    svc = _service(fill_probability_on_touch=None)
    intent = _intent(side="sell", limit_price=Decimal("100"))
    # bar.open (97) < limit (100), bar.high (101) >= limit → intrabar touch
    bar = _bar(open_=Decimal("97"), high=Decimal("101"), low=Decimal("95"), close=Decimal("98"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")


def test_intrabar_buy_fill_price_is_limit_price_not_bar_open():
    svc = _service()
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("104"), high=Decimal("106"), low=Decimal("99"), close=Decimal("103"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")
    assert batch.fills[0].price != Decimal("104")  # not bar.open


def test_no_fill_when_bar_does_not_reach_buy_limit():
    svc = _service()
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("103"), high=Decimal("106"), low=Decimal("101"), close=Decimal("104"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills == []
    assert batch.carry_forward == []  # silently dropped, strategy re-evaluates


def test_no_fill_when_bar_does_not_reach_sell_limit():
    svc = _service()
    intent = _intent(side="sell", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("95"), high=Decimal("98"), low=Decimal("93"), close=Decimal("96"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills == []
    assert batch.carry_forward == []


# ---------------------------------------------------------------------------
# Intrabar touch — probability enabled
# ---------------------------------------------------------------------------


def test_intrabar_touch_probability_one_always_fills():
    """fill_probability_on_touch=1.0 → every intrabar touch fills."""
    bar = _bar(open_=Decimal("102"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))
    for seed in range(20):
        svc = _service(fill_probability_on_touch=1.0, seed=seed)
        batch = svc.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        assert len(batch.fills) == 1, f"seed={seed} should always fill at probability=1.0"


def test_intrabar_touch_probability_low_produces_some_rejections():
    """fill_probability_on_touch=0.1 → most touches are rejected over many draws."""
    bar = _bar(open_=Decimal("102"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))
    fills = 0
    rejects = 0
    for seed in range(100):
        svc = _service(fill_probability_on_touch=0.1, seed=seed)
        batch = svc.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        if batch.fills:
            fills += 1
        else:
            rejects += 1
    # With p=0.1 over 100 trials we expect ~10 fills; sanity-check bounds
    assert fills < 40, "fill rate too high for p=0.1"
    assert rejects > 50, "too few rejections for p=0.1"


def test_intrabar_touch_rejected_carries_forward_full_quantity():
    """When touch probability rejects the fill, full qty must carry forward."""
    bar = _bar(open_=Decimal("102"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))
    # Use seed that produces rng.random() >= 0.5 on first call → rejection
    rejected = False
    for seed in range(200):
        svc = _service(fill_probability_on_touch=0.5, seed=seed)
        intent = _intent(side="buy", limit_price=Decimal("100"), qty=Decimal("15"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            # This seed caused a rejection
            assert len(batch.carry_forward) == 1
            assert batch.carry_forward[0].qty == Decimal("15")
            rejected = True
            break
    assert rejected, "Expected at least one rejection in 200 seeds at p=0.5"


def test_intrabar_touch_accepted_fills_and_carry_forward_empty():
    """When touch probability accepts, carry_forward should be empty for full-fill."""
    bar = _bar(open_=Decimal("102"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))
    accepted = False
    for seed in range(200):
        svc = _service(fill_probability_on_touch=0.5, seed=seed)
        intent = _intent(side="buy", limit_price=Decimal("100"), qty=Decimal("10"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if batch.fills:
            assert batch.carry_forward == []
            assert batch.fills[0].quantity == Decimal("10")
            accepted = True
            break
    assert accepted, "Expected at least one acceptance in 200 seeds at p=0.5"


def test_intrabar_sell_touch_rejected_carries_forward():
    bar = _bar(open_=Decimal("97"), high=Decimal("101"), low=Decimal("95"), close=Decimal("98"))
    rejected = False
    for seed in range(200):
        svc = _service(fill_probability_on_touch=0.5, seed=seed)
        intent = _intent(side="sell", limit_price=Decimal("100"), qty=Decimal("8"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            assert len(batch.carry_forward) == 1
            assert batch.carry_forward[0].qty == Decimal("8")
            rejected = True
            break
    assert rejected


# ---------------------------------------------------------------------------
# Exact-touch behavior
# ---------------------------------------------------------------------------


def test_exact_touch_buy_no_probability_fills():
    """bar.low == limit_price with no probability gate → legacy: fills."""
    svc = _service(fill_probability_on_touch=None)
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("103"), high=Decimal("105"), low=Decimal("100"), close=Decimal("102"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")


def test_exact_touch_sell_no_probability_fills():
    """bar.high == limit_price with no probability gate → legacy: fills."""
    svc = _service(fill_probability_on_touch=None)
    intent = _intent(side="sell", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("97"), high=Decimal("100"), low=Decimal("95"), close=Decimal("98"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("100")


def test_exact_touch_buy_with_probability_is_gated():
    """bar.low == limit_price with probability set → some seeds reject, some accept."""
    bar = _bar(open_=Decimal("103"), high=Decimal("105"), low=Decimal("100"), close=Decimal("102"))
    results = []
    for seed in range(50):
        svc = _service(fill_probability_on_touch=0.5, seed=seed)
        batch = svc.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        results.append(bool(batch.fills))
    # Should see both fills and non-fills across 50 seeds
    assert any(results), "Expected at least one fill"
    assert not all(results), "Expected at least one rejection"


def test_exact_touch_sell_with_probability_is_gated():
    bar = _bar(open_=Decimal("97"), high=Decimal("100"), low=Decimal("95"), close=Decimal("98"))
    results = []
    for seed in range(50):
        svc = _service(fill_probability_on_touch=0.5, seed=seed)
        batch = svc.fill(
            order_intents=[_intent(side="sell", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        results.append(bool(batch.fills))
    assert any(results)
    assert not all(results)


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------


def test_deterministic_replay_intrabar_touch():
    """Same seed → identical fill/reject decisions, prices, quantities."""
    bar = _bar(open_=Decimal("102"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))

    def run(seed: int):
        svc = _service(fill_probability_on_touch=0.5, seed=seed)
        return svc.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"), qty=Decimal("10"))],
            bars_at_timestamp={_SYMBOL: bar},
        )

    for seed in range(20):
        batch_a = run(seed)
        batch_b = run(seed)
        assert len(batch_a.fills) == len(batch_b.fills), f"fill count differs at seed={seed}"
        assert len(batch_a.carry_forward) == len(batch_b.carry_forward)
        if batch_a.fills:
            assert batch_a.fills[0].price == batch_b.fills[0].price
            assert batch_a.fills[0].quantity == batch_b.fills[0].quantity


def test_deterministic_replay_gap_open():
    """Gap-open replay is deterministic regardless of seed (no RNG involved)."""
    bar = _bar(open_=Decimal("97"), high=Decimal("103"), low=Decimal("96"), close=Decimal("101"))
    for seed in range(10):
        svc1 = _service(fill_probability_on_touch=0.5, seed=seed)
        svc2 = _service(fill_probability_on_touch=0.5, seed=seed)
        b1 = svc1.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        b2 = svc2.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        assert len(b1.fills) == 1
        assert len(b2.fills) == 1
        assert b1.fills[0].price == b2.fills[0].price


def test_different_seeds_may_diverge():
    """Different seeds can produce different touch outcomes."""
    bar = _bar(open_=Decimal("102"), high=Decimal("105"), low=Decimal("99"), close=Decimal("103"))
    outcomes = set()
    for seed in range(30):
        svc = _service(fill_probability_on_touch=0.5, seed=seed)
        batch = svc.fill(
            order_intents=[_intent(side="buy", limit_price=Decimal("100"))],
            bars_at_timestamp={_SYMBOL: bar},
        )
        outcomes.add(bool(batch.fills))
    assert len(outcomes) == 2, "Expected both fill and reject outcomes across different seeds"


# ---------------------------------------------------------------------------
# Integration: latency_bars
# ---------------------------------------------------------------------------


def test_gap_open_buy_with_latency_bars_1():
    """Gap-open fills use bar.open regardless of latency_bars setting."""
    svc = _service(latency_bars=1)
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("97"), high=Decimal("103"), low=Decimal("96"), close=Decimal("101"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("97")


def test_gap_open_buy_with_latency_bars_2():
    svc = _service(latency_bars=2)
    intent = _intent(side="buy", limit_price=Decimal("100"))
    bar = _bar(open_=Decimal("95"), high=Decimal("102"), low=Decimal("94"), close=Decimal("99"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].price == Decimal("95")


def test_intrabar_touch_with_latency_bars():
    """latency_bars does not affect limit order fill price (always limit_price for intrabar)."""
    for latency in (0, 1, 2):
        svc = _service(latency_bars=latency)
        intent = _intent(side="buy", limit_price=Decimal("100"))
        bar = _bar(
            open_=Decimal("103"), high=Decimal("106"), low=Decimal("99"), close=Decimal("104")
        )

        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

        assert len(batch.fills) == 1, f"latency_bars={latency}"
        assert batch.fills[0].price == Decimal("100"), f"latency_bars={latency}"


# ---------------------------------------------------------------------------
# Integration: volume participation cap
# ---------------------------------------------------------------------------


def test_gap_open_buy_respects_participation_cap():
    """Volume participation cap applies after gap-open eligibility is confirmed."""
    svc = _service(max_volume_participation_rate=0.01)  # 1% of 1000 = 10 shares max
    intent = _intent(side="buy", limit_price=Decimal("100"), qty=Decimal("50"))
    bar = _bar(
        open_=Decimal("98"),
        high=Decimal("103"),
        low=Decimal("97"),
        close=Decimal("101"),
        volume=1_000,
    )

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity == Decimal("10")  # 1% of 1000
    assert batch.fills[0].price == Decimal("98")  # still bar.open
    assert len(batch.carry_forward) == 1
    assert batch.carry_forward[0].qty == Decimal("40")


def test_intrabar_touch_respects_participation_cap():
    svc = _service(max_volume_participation_rate=0.10)  # 10% of 500 = 50 shares
    intent = _intent(side="sell", limit_price=Decimal("100"), qty=Decimal("100"))
    bar = _bar(
        open_=Decimal("97"),
        high=Decimal("101"),
        low=Decimal("95"),
        close=Decimal("98"),
        volume=500,
    )

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity == Decimal("50")
    assert len(batch.carry_forward) == 1
    assert batch.carry_forward[0].qty == Decimal("50")


def test_gap_open_zero_volume_carries_forward():
    """Zero bar volume → participation cap yields 0 → carry forward entire qty."""
    svc = _service(max_volume_participation_rate=0.10)
    intent = _intent(side="buy", limit_price=Decimal("100"), qty=Decimal("20"))
    bar = _bar(
        open_=Decimal("98"),
        high=Decimal("103"),
        low=Decimal("97"),
        close=Decimal("101"),
        volume=0,
    )

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert batch.fills == []
    assert len(batch.carry_forward) == 1
    assert batch.carry_forward[0].qty == Decimal("20")


# ---------------------------------------------------------------------------
# Integration: probabilistic partial fills (F-01) after limit eligibility
# ---------------------------------------------------------------------------


def test_gap_open_buy_with_partial_fill_probability():
    """F-01 partial fill is applied after gap-open eligibility; carry forward remainder."""
    # seed=0 → rng.random() should trigger partial fill with prob=1.0
    svc = _service(partial_fill_probability=1.0, seed=42)
    intent = _intent(side="buy", limit_price=Decimal("100"), qty=Decimal("100"))
    bar = _bar(open_=Decimal("97"), high=Decimal("103"), low=Decimal("96"), close=Decimal("101"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    # partial_fill_probability=1.0 → always partial
    assert len(batch.fills) == 1
    assert batch.fills[0].quantity < Decimal("100")
    assert batch.fills[0].price == Decimal("97")  # gap-open price preserved
    assert len(batch.carry_forward) == 1
    total = batch.fills[0].quantity + batch.carry_forward[0].qty
    assert total == Decimal("100")


def test_intrabar_touch_with_partial_fill_probability():
    svc = _service(partial_fill_probability=1.0, seed=42)
    intent = _intent(side="sell", limit_price=Decimal("100"), qty=Decimal("50"))
    bar = _bar(open_=Decimal("97"), high=Decimal("101"), low=Decimal("95"), close=Decimal("98"))

    batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})

    assert len(batch.fills) == 1
    assert batch.fills[0].quantity < Decimal("50")
    assert batch.fills[0].price == Decimal("100")
    total = batch.fills[0].quantity + batch.carry_forward[0].qty
    assert total == Decimal("50")


# ---------------------------------------------------------------------------
# Integration: touch probability + participation cap interact correctly
# ---------------------------------------------------------------------------


def test_touch_probability_rejection_skips_participation_cap():
    """When touch probability rejects, carry_forward uses original_qty not capped qty."""
    # Use low volume to ensure cap would bite, but touch rejection means we never get there
    bar = _bar(
        open_=Decimal("103"),
        high=Decimal("105"),
        low=Decimal("99"),
        close=Decimal("102"),
        volume=10,
    )
    rejected = False
    for seed in range(200):
        svc = _service(fill_probability_on_touch=0.5, max_volume_participation_rate=0.01, seed=seed)
        intent = _intent(side="buy", limit_price=Decimal("100"), qty=Decimal("100"))
        batch = svc.fill(order_intents=[intent], bars_at_timestamp={_SYMBOL: bar})
        if not batch.fills:
            # Rejected by touch probability → full original qty carries forward
            assert batch.carry_forward[0].qty == Decimal("100")
            rejected = True
            break
    assert rejected
