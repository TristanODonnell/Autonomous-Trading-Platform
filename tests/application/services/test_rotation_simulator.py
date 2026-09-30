"""Offline rotation simulator (portfolio rotation step 5D)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from autonomous_trading_platform.application.services.rotation_simulator import (
    RotationSimulator,
    SimConfig,
    grid_configs,
    rank,
)
from autonomous_trading_platform.contracts.governance.rotation_dataset import (
    RotationDataset,
    RotationFill,
    RotationStrategySeries,
)

START = date(2024, 1, 1)
DAYS = 140


def _days() -> list[date]:
    return [START + timedelta(days=i) for i in range(DAYS)]


def _curve(daily_return: float, *, wobble: float = 0.004) -> list[tuple[datetime, float]]:
    """Two bars a day; a steady drift plus an alternating wobble (keeps std > 0)."""
    points, value = [], 100_000.0
    for i, day in enumerate(_days()):
        for hour, sign in ((15, 1), (20, -1)):
            value *= 1 + daily_return / 2 + sign * wobble * (1 if i % 2 else -1) / 2
            points.append((datetime(day.year, day.month, day.day, hour, tzinfo=UTC), value))
    return points


def _fills(n_per_week: int = 3) -> list[RotationFill]:
    fills = []
    for i, day in enumerate(_days()):
        if i % 7 >= n_per_week:
            continue
        ts = datetime(day.year, day.month, day.day, 16, tzinfo=UTC)
        fills.append(RotationFill(timestamp=ts, symbol="AAPL", side="buy", quantity=1, price=100))
        fills.append(
            RotationFill(
                timestamp=ts + timedelta(hours=3), symbol="AAPL", side="sell", quantity=1, price=101
            )
        )
    return fills


def _forward(daily_return: float, wobble: float = 0.004) -> dict[date, float]:
    return {day: daily_return + wobble * (1 if i % 2 else -1) for i, day in enumerate(_days()) if i}


def _series(sid: str, daily_return: float, **kw: object) -> RotationStrategySeries:
    forward_return = kw.pop("forward_return", daily_return)
    return RotationStrategySeries(
        strategy_id=sid,
        strategy_type="momentum",
        available_from=kw.pop("available_from", START),  # type: ignore[arg-type]
        equity=_curve(daily_return),
        fills=_fills(),
        approval_score=1.2,
        forward_returns=_forward(float(forward_return)),  # type: ignore[arg-type]
        forward_closed={day: (1, 1) for day in _days()},
        **kw,  # type: ignore[arg-type]
    )


def _dataset(strategies: list[RotationStrategySeries], initial: list[str]) -> RotationDataset:
    reviews = [
        datetime(d.year, d.month, d.day, 21, tzinfo=UTC)
        for d in _days()[21::7]  # weekly from day 21
    ]
    return RotationDataset(
        start_date=START,
        end_date=_days()[-1],
        starting_cash=100_000,
        re_sim_initial_cash=100_000,
        review_dates=reviews,
        settings={"max_total_strategy_allocation_pct": 1.0, "per_strategy_cap": None},
        initial_active=initial,
        strategies=strategies,
    )


# Weak incumbent a (flat), strong incumbent b, strong challenger c (bench from day 0).
def _swap_dataset(*, promotable: bool = True) -> RotationDataset:
    return _dataset(
        [
            _series("a", 0.0, seeded_approved=True),
            _series("b", 0.003, seeded_approved=True),
            _series("c", 0.004, promotable=promotable),
        ],
        initial=["a", "b"],
    )


FAST = SimConfig(
    min_active=2,
    max_active=2,
    max_on_deck=2,
    swap_consecutive=3,
    min_tenure_days=30,
    swap_interval_days=28,
    min_shadow_days=20,
    min_shadow_trades=5,
    on_deck_min_tenure_days=7,
)


def test_baseline_holds_the_initial_set() -> None:
    sim = RotationSimulator(_swap_dataset())

    result = sim.run(SimConfig(mode="off"))

    assert result.swaps == 0
    assert result.final_tiers == {"a": "active", "b": "active", "c": "bench"}
    # Half in a (flat) and half in b: the portfolio earns about half of b's drift.
    b_only = sim.daily["b"]
    b_return = b_only.iloc[-1] / b_only.iloc[0] - 1
    assert result.metrics is not None
    assert result.metrics.total_return == pytest.approx(b_return / 2, rel=0.2)


def test_obvious_challenger_swaps_in_for_the_weak_incumbent() -> None:
    result = RotationSimulator(_swap_dataset()).run(FAST)

    swaps = [d for d in result.decisions if d["type"] == "swap" and d["applied"]]
    assert swaps and swaps[0]["strategy_id"] == "c" and swaps[0]["counterpart_id"] == "a"
    assert swaps[0]["streak"] >= 3
    assert result.final_tiers["c"] == "active"
    assert result.final_tiers["a"] == "on_deck"


def test_rotation_beats_the_baseline_when_the_challenger_is_better() -> None:
    sim = RotationSimulator(_swap_dataset())

    rotated, baseline = sim.run(FAST), sim.run(SimConfig(mode="off"))

    assert rotated.metrics is not None and baseline.metrics is not None
    assert rotated.metrics.total_return > baseline.metrics.total_return


def test_unpromotable_candidate_is_refused_and_nothing_moves() -> None:
    result = RotationSimulator(_swap_dataset(promotable=False)).run(FAST)

    assert result.swaps == 0
    assert result.governance_rejections >= 1
    assert result.final_tiers["a"] == "active"
    assert any(d["reason"].endswith("governance_rejected") for d in result.decisions)


def test_churn_guardrails_bind() -> None:
    sim = RotationSimulator(_swap_dataset())

    strict = sim.run(
        SimConfig(**{**FAST.__dict__, "swap_consecutive": 30})  # longer than the run
    )
    long_tenure = sim.run(SimConfig(**{**FAST.__dict__, "min_tenure_days": 400}))

    assert strict.swaps == 0
    assert long_tenure.swaps == 0


def test_candidate_arriving_later_joins_the_bench_then() -> None:
    later = START + timedelta(days=60)
    dataset = _dataset(
        [
            _series("a", 0.0, seeded_approved=True),
            _series("b", 0.003, seeded_approved=True),
            _series("c", 0.004, promotable=True, available_from=later),
        ],
        initial=["a", "b"],
    )

    result = RotationSimulator(dataset).run(FAST)

    first_c = min(
        datetime.fromisoformat(d["at"]) for d in result.decisions if d["strategy_id"] == "c"
    )
    assert first_c.date() >= later


def test_resim_evidence_is_shared_across_configs() -> None:
    sim = RotationSimulator(_swap_dataset())
    sim.run(FAST)
    cached = len(sim._resim_cache)

    sim.run(SimConfig(**{**FAST.__dict__, "swap_margin": 0.2}))

    assert len(sim._resim_cache) == cached


def test_grid_and_rank() -> None:
    configs = grid_configs(FAST)
    sim = RotationSimulator(_swap_dataset())
    results = [sim.run(c) for c in configs[:6]] + [sim.run(SimConfig(mode="off"))]

    ranked = rank(results, max_drawdown=0.5, max_swaps_per_month=5)

    assert len(configs) == 3 * 3 * 3 * 2 * 3 * 2
    assert {(c.min_active, c.max_active) for c in configs} == {(3, 3), (3, 5)}
    sharpes = [r.metrics.sharpe for r in ranked if r.metrics and r.metrics.sharpe is not None]
    assert sharpes == sorted(sharpes, reverse=True)


def test_portfolio_is_valued_from_the_forward_record_not_the_resim() -> None:
    # Re-sim says a earns 0.3 %/day; its forward record (how it traded) is flat.
    dataset = _dataset(
        [_series("a", 0.003, seeded_approved=True, forward_return=0.0)], initial=["a"]
    )

    result = RotationSimulator(dataset).run(SimConfig(mode="off"))

    assert result.metrics is not None
    assert abs(result.metrics.total_return) < 0.01
    assert result.fallback_days == 0


def test_days_without_a_forward_record_fall_back_to_the_resim_and_are_counted() -> None:
    series = _series("a", 0.003, seeded_approved=True)
    series.forward_returns = {}
    dataset = _dataset([series], initial=["a"])

    result = RotationSimulator(dataset).run(SimConfig(mode="off"))

    assert result.fallback_days > 0
    assert result.metrics is not None and result.metrics.total_return > 0.2


def test_water_fill_matches_the_reallocation_service() -> None:
    from autonomous_trading_platform.application.services.rotation_simulator import _water_fill

    weights = _water_fill({"a": 3.0, "b": 1.0, "c": 1.0}, total=1.0, cap=0.35)

    # a is capped at 0.35; the remaining 0.65 is split equally between b and c.
    assert weights == pytest.approx({"a": 0.35, "b": 0.325, "c": 0.325})


def test_auto_mode_reweights_toward_the_stronger_strategy() -> None:
    dataset = _dataset(
        [_series("a", 0.0, seeded_approved=True), _series("b", 0.004, seeded_approved=True)],
        initial=["a", "b"],
    )
    dataset.settings = {**dataset.settings, "min_allocation_change_pct": 0.02}
    sim = RotationSimulator(dataset)

    rotated = sim.run(SimConfig(**{**FAST.__dict__, "swap_consecutive": 30}))
    baseline = sim.run(SimConfig(mode="off"))

    assert rotated.swaps == 0
    assert rotated.metrics is not None and baseline.metrics is not None
    # Same set, more weight on b: better than the equal-weight baseline.
    assert rotated.metrics.total_return > baseline.metrics.total_return


def test_bootstrap_respects_max_active() -> None:
    dataset = _dataset(
        [_series(sid, 0.001, seeded_approved=True) for sid in ("a", "b", "c")],
        initial=["a", "b", "c"],
    )

    result = RotationSimulator(dataset).run(SimConfig(mode="off", min_active=1, max_active=2))

    assert result.final_tiers == {"a": "active", "b": "active", "c": "on_deck"}
