"""Research <-> trading-cycle parity (rotation step 5c).

Same strategy + same bars => same signals and same trades in the research simulator and
in the platform trading cycle. See docs/roadmaps/portfolio-rotation-step5c-research-parity.md
(findings F1-F5); every gap is fixed, so both levels must match exactly.
"""

from __future__ import annotations

import pytest

from tests.utilities.parity_harness import (
    ParityStrategy,
    describe_mismatch,
    platform_cycle_fills,
    platform_signals,
    research_run,
    seed_platform_state,
    seed_strategy,
    seed_universe,
    write_parity_bars,
)

# EMA is path-dependent: its value depends on how many bars are passed, not only on
# having enough of them (warmup 13 here, under research's old fixed 20).
_COMPOSITE_EMA_CROSS = {
    "strategy_type": "composite_rule",
    "indicators": [
        {"id": "ema_fast", "component": "exponential_moving_average", "parameters": {"window": 5}},
        {"id": "ema_slow", "component": "exponential_moving_average", "parameters": {"window": 12}},
    ],
    "entry_rules": [
        {
            "id": "ema_cross",
            "component": "crossover",
            "inputs": {
                "previous_fast": {"indicator_id": "ema_fast", "offset": -1},
                "previous_slow": {"indicator_id": "ema_slow", "offset": -1},
                "current_fast": {"indicator_id": "ema_fast"},
                "current_slow": {"indicator_id": "ema_slow"},
            },
            "parameters": {"confidence": 0.6},
        },
    ],
    "aggregator": {"component": "voting", "parameters": {"min_votes": 1}},
}

STRATEGIES = {
    "momentum": ParityStrategy("parity_momentum", "momentum", {"lookback": 5}),
    "mean_reversion": ParityStrategy(
        "parity_mean_reversion",
        "mean_reversion",
        {"window": 20, "buy_below_z": -1.5, "sell_above_z": 1.5},
    ),
    "ma_crossover": ParityStrategy(
        "parity_ma_crossover", "moving_average_crossover", {"short_window": 10, "long_window": 30}
    ),
    "factor_long_windows": ParityStrategy(
        "parity_factor",
        "factor_based",
        {
            "momentum_lookback": 1,
            "mean_reversion_window": 100,
            "volatility_window": 100,
            "volume_window": 100,
            "buy_score_threshold": 0.1,
            "sell_score_threshold": -1.0,
            "momentum_weight": 1.0,
            "mean_reversion_weight": 1.0,
            "volume_weight": 1.0,
            "volatility_weight": 1.0,
        },
    ),
    "composite_ema": ParityStrategy("parity_composite", "composite_rule", _COMPOSITE_EMA_CROSS),
}


@pytest.fixture()
def parity_data(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_parity_bars(tmp_path / "data")
    return tmp_path


@pytest.mark.parametrize("name", sorted(STRATEGIES))
def test_same_bars_same_signals(name, parity_data, db_session) -> None:
    strategy = STRATEGIES[name]
    seed_universe(db_session)
    seed_strategy(db_session, strategy)

    research_signals, _ = research_run(db_session, strategy)
    platform = platform_signals(db_session, strategy)

    assert platform, "the platform path produced no signals; the fixture data is too flat"
    assert research_signals == platform, describe_mismatch("signals", research_signals, platform)


# momentum signals every bar (buys, sells and resizes all day); the crossover and mean
# reversion signal only on entry and exit bars and must hold in between (F6).
@pytest.mark.parametrize("name", ["momentum", "ma_crossover", "mean_reversion"])
def test_same_bars_same_trades(name, parity_data, db_session, monkeypatch) -> None:
    strategy = STRATEGIES[name]
    seed_platform_state(db_session, monkeypatch)
    seed_strategy(db_session, strategy)

    _, research_fills = research_run(db_session, strategy)
    platform_fills = platform_cycle_fills(db_session)

    assert platform_fills, "the platform cycle made no trades"
    assert research_fills == platform_fills, describe_mismatch(
        "fills", research_fills, platform_fills
    )
