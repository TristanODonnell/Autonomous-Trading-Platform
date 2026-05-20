"""All strategy registrations for the canonical StrategyRegistry.

Importing this module registers every built-in strategy and locks the
registry.  It is imported exactly once, from __init__.py.

Registration order is preserved and stable — do not reorder entries.
Add new strategies at the bottom of their family group.
"""

from __future__ import annotations

from typing import Any

from autonomous_trading_platform.strategy.implementations.factor_based_strategy import (
    FactorBasedStrategy,
)
from autonomous_trading_platform.strategy.implementations.intentional_loser_strategy import (
    IntentionalLoserStrategy,
)
from autonomous_trading_platform.strategy.implementations.mean_reversion_strategy import (
    MeanReversionStrategy,
)
from autonomous_trading_platform.strategy.implementations.momentum_strategy import (
    MomentumStrategy,
)
from autonomous_trading_platform.strategy.implementations.moving_average_crossover_strategy import (
    MovingAverageCrossoverStrategy,
)
from autonomous_trading_platform.strategy.implementations.random_debug_strategy import (
    RandomStrategy,
)
from autonomous_trading_platform.strategy.implementations.stub_strategy import StubStrategy

from .parameter_metadata import ParameterSpec, ParameterType
from .parameter_schemas import (
    FactorBasedParameters,
    IntentionalLoserParameters,
    MeanReversionParameters,
    MomentumParameters,
    MovingAverageCrossoverParameters,
    RandomParameters,
    StubParameters,
)
from .strategy_definition import StrategyDefinition
from .strategy_family import StrategyFamily
from .strategy_registry import _REGISTRY
from .validators import (
    _validate_factor_based,
    _validate_intentional_loser,
    _validate_mean_reversion,
    _validate_momentum,
    _validate_moving_average_crossover,
    _validate_random,
    _validate_stub,
)

# ---------------------------------------------------------------------------
# Builder functions — one per strategy type
# ---------------------------------------------------------------------------


def _build_stub(strategy_id: str, params: dict[str, Any]) -> StubStrategy:
    return StubStrategy(
        strategy_id=strategy_id,
        price_change_threshold=params["price_change_threshold"],
    )


def _build_intentional_loser(strategy_id: str, params: dict[str, Any]) -> IntentionalLoserStrategy:
    return IntentionalLoserStrategy(
        strategy_id=strategy_id,
        price_change_threshold=params["price_change_threshold"],
    )


def _build_random(strategy_id: str, params: dict[str, Any]) -> RandomStrategy:
    return RandomStrategy(
        strategy_id=strategy_id,
        signal_probability=params["signal_probability"],
        buy_probability=params["buy_probability"],
        random_seed=params["random_seed"],
    )


def _build_moving_average_crossover(
    strategy_id: str, params: dict[str, Any]
) -> MovingAverageCrossoverStrategy:
    return MovingAverageCrossoverStrategy(
        strategy_id=strategy_id,
        short_window=params["short_window"],
        long_window=params["long_window"],
    )


def _build_momentum(strategy_id: str, params: dict[str, Any]) -> MomentumStrategy:
    return MomentumStrategy(
        strategy_id=strategy_id,
        lookback=params["lookback"],
        buy_above=params["buy_above"],
        sell_below=params["sell_below"],
    )


def _build_mean_reversion(strategy_id: str, params: dict[str, Any]) -> MeanReversionStrategy:
    return MeanReversionStrategy(
        strategy_id=strategy_id,
        window=params["window"],
        buy_below_z=params["buy_below_z"],
        sell_above_z=params["sell_above_z"],
    )


def _build_factor_based(strategy_id: str, params: dict[str, Any]) -> FactorBasedStrategy:
    return FactorBasedStrategy(
        strategy_id=strategy_id,
        momentum_lookback=params["momentum_lookback"],
        mean_reversion_window=params["mean_reversion_window"],
        volatility_window=params["volatility_window"],
        volume_window=params["volume_window"],
        buy_score_threshold=params["buy_score_threshold"],
        sell_score_threshold=params["sell_score_threshold"],
        momentum_weight=params["momentum_weight"],
        mean_reversion_weight=params["mean_reversion_weight"],
        volume_weight=params["volume_weight"],
        volatility_weight=params["volatility_weight"],
    )


# ---------------------------------------------------------------------------
# Warmup bar functions — compute minimum bars needed from parameters
# ---------------------------------------------------------------------------


def _warmup_stub(p: dict[str, Any]) -> int:
    return 1


def _warmup_intentional_loser(p: dict[str, Any]) -> int:
    return 1


def _warmup_random(p: dict[str, Any]) -> int:
    return 0


def _warmup_moving_average_crossover(p: dict[str, Any]) -> int:
    # needs long_window bars plus one previous bar to detect a crossover
    return int(p.get("long_window", 30)) + 1


def _warmup_momentum(p: dict[str, Any]) -> int:
    # momentum() requires len(values) > lookback
    return int(p.get("lookback", 5)) + 1


def _warmup_mean_reversion(p: dict[str, Any]) -> int:
    # z_score requires window bars
    return int(p.get("window", 20))


def _warmup_factor_based(p: dict[str, Any]) -> int:
    # all four indicators must have sufficient data
    return max(
        int(p.get("momentum_lookback", 5)) + 1,
        int(p.get("mean_reversion_window", 20)),
        int(p.get("volatility_window", 20)),
        int(p.get("volume_window", 20)),
    )


# ---------------------------------------------------------------------------
# Registrations
# ---------------------------------------------------------------------------

_REGISTRY.register(
    StrategyDefinition(
        strategy_type="stub",
        display_name="Stub",
        description="Minimal placeholder that validates the strategy architecture.",
        family=StrategyFamily.DEBUG,
        implementation_class=StubStrategy,
        debug=True,
        production_ready=False,
        default_parameters={"price_change_threshold": 0.0},
        parameter_validator=_validate_stub,
        parameter_schema=StubParameters,
        warmup_bars_fn=_warmup_stub,
        required_indicators=(),
        required_persisted_features=(),
        parameter_specs=(
            ParameterSpec(
                name="price_change_threshold",
                parameter_type=ParameterType.FLOAT,
                default=0.0,
                description="Minimum absolute price change to generate a signal.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=False,
                mutation_strategy="fixed",
            ),
        ),
        builder=_build_stub,
        deterministic=True,
    )
)

_REGISTRY.register(
    StrategyDefinition(
        strategy_type="intentional_loser",
        display_name="Intentional Loser",
        description="Always trades against the signal; used to confirm negative-edge detection.",
        family=StrategyFamily.DEBUG,
        implementation_class=IntentionalLoserStrategy,
        debug=True,
        production_ready=False,
        default_parameters={"price_change_threshold": 0.0},
        parameter_validator=_validate_intentional_loser,
        parameter_schema=IntentionalLoserParameters,
        warmup_bars_fn=_warmup_intentional_loser,
        required_indicators=(),
        required_persisted_features=(),
        parameter_specs=(
            ParameterSpec(
                name="price_change_threshold",
                parameter_type=ParameterType.FLOAT,
                default=0.0,
                description="Minimum absolute price change to generate an inverted signal.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=False,
                mutation_strategy="fixed",
            ),
        ),
        builder=_build_intentional_loser,
        deterministic=True,
    )
)

_REGISTRY.register(
    StrategyDefinition(
        strategy_type="random",
        display_name="Random",
        description="Random baseline; expected edge ≈ 0 before costs.",
        family=StrategyFamily.DEBUG,
        implementation_class=RandomStrategy,
        debug=True,
        production_ready=False,
        default_parameters={
            "signal_probability": 0.33,
            "buy_probability": 0.5,
            "random_seed": None,
        },
        parameter_validator=_validate_random,
        parameter_schema=RandomParameters,
        warmup_bars_fn=_warmup_random,
        required_indicators=(),
        required_persisted_features=(),
        parameter_specs=(
            ParameterSpec(
                name="signal_probability",
                parameter_type=ParameterType.FLOAT,
                default=0.33,
                description="Probability of emitting a signal on each bar.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=False,
                mutation_strategy="fixed",
            ),
            ParameterSpec(
                name="buy_probability",
                parameter_type=ParameterType.FLOAT,
                default=0.5,
                description="Conditional probability of BUY given a signal is emitted.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=False,
                mutation_strategy="fixed",
            ),
            ParameterSpec(
                name="random_seed",
                parameter_type=ParameterType.INT,
                default=None,
                description="Optional deterministic seed for the strategy-local random generator.",
                discrete=True,
                step=1,
                tunable=False,
                mutation_strategy="fixed",
            ),
        ),
        builder=_build_random,
        deterministic=False,
    )
)

_REGISTRY.register(
    StrategyDefinition(
        strategy_type="moving_average_crossover",
        display_name="Moving Average Crossover",
        description="Buys when the short MA crosses above the long MA, sells on the reverse.",
        family=StrategyFamily.TREND,
        implementation_class=MovingAverageCrossoverStrategy,
        debug=False,
        production_ready=True,
        default_parameters={"short_window": 10, "long_window": 30},
        parameter_validator=_validate_moving_average_crossover,
        parameter_schema=MovingAverageCrossoverParameters,
        warmup_bars_fn=_warmup_moving_average_crossover,
        required_indicators=("simple_moving_average",),
        required_persisted_features=(),
        parameter_specs=(
            ParameterSpec(
                name="short_window",
                parameter_type=ParameterType.INT,
                default=10,
                description="Lookback bars for the fast moving average.",
                min_value=2,
                max_value=50,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
            ParameterSpec(
                name="long_window",
                parameter_type=ParameterType.INT,
                default=30,
                description="Lookback bars for the slow moving average.",
                min_value=10,
                max_value=200,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
        ),
        builder=_build_moving_average_crossover,
        deterministic=True,
    )
)

_REGISTRY.register(
    StrategyDefinition(
        strategy_type="momentum",
        display_name="Momentum",
        description="Buys on positive n-period return, sells on negative return.",
        family=StrategyFamily.MOMENTUM,
        implementation_class=MomentumStrategy,
        debug=False,
        production_ready=True,
        default_parameters={"lookback": 5, "buy_above": 0.0, "sell_below": 0.0},
        parameter_validator=_validate_momentum,
        parameter_schema=MomentumParameters,
        warmup_bars_fn=_warmup_momentum,
        required_indicators=("momentum",),
        required_persisted_features=(),
        parameter_specs=(
            ParameterSpec(
                name="lookback",
                parameter_type=ParameterType.INT,
                default=5,
                description="Return lookback period in bars.",
                min_value=1,
                max_value=60,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
            ParameterSpec(
                name="buy_above",
                parameter_type=ParameterType.FLOAT,
                default=0.0,
                description="Minimum return required to emit a BUY signal.",
                min_value=-0.1,
                max_value=0.1,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
            ParameterSpec(
                name="sell_below",
                parameter_type=ParameterType.FLOAT,
                default=0.0,
                description="Maximum return below which to emit a SELL signal.",
                min_value=-0.1,
                max_value=0.1,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
        ),
        builder=_build_momentum,
        deterministic=True,
    )
)

_REGISTRY.register(
    StrategyDefinition(
        strategy_type="mean_reversion",
        display_name="Mean Reversion",
        description="Buys when price is two standard deviations below the rolling mean.",
        family=StrategyFamily.MEAN_REVERSION,
        implementation_class=MeanReversionStrategy,
        debug=False,
        production_ready=True,
        default_parameters={"window": 20, "buy_below_z": -2.0, "sell_above_z": 2.0},
        parameter_validator=_validate_mean_reversion,
        parameter_schema=MeanReversionParameters,
        warmup_bars_fn=_warmup_mean_reversion,
        required_indicators=("z_score", "simple_moving_average", "rolling_standard_deviation"),
        required_persisted_features=(),
        parameter_specs=(
            ParameterSpec(
                name="window",
                parameter_type=ParameterType.INT,
                default=20,
                description="Rolling window for mean and standard deviation.",
                min_value=5,
                max_value=100,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
            ParameterSpec(
                name="buy_below_z",
                parameter_type=ParameterType.FLOAT,
                default=-2.0,
                description="Z-score threshold below which to emit a BUY signal.",
                min_value=-4.0,
                max_value=-0.5,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
            ParameterSpec(
                name="sell_above_z",
                parameter_type=ParameterType.FLOAT,
                default=2.0,
                description="Z-score threshold above which to emit a SELL signal.",
                min_value=0.5,
                max_value=4.0,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
        ),
        builder=_build_mean_reversion,
        deterministic=True,
    )
)

_REGISTRY.register(
    StrategyDefinition(
        strategy_type="factor_based",
        display_name="Factor Based",
        description="Scores each bar across momentum, mean-reversion, volume, and volatility factors.",
        family=StrategyFamily.FACTOR,
        implementation_class=FactorBasedStrategy,
        debug=False,
        production_ready=True,
        default_parameters={
            "momentum_lookback": 5,
            "mean_reversion_window": 20,
            "volatility_window": 20,
            "volume_window": 20,
            "buy_score_threshold": 0.5,
            "sell_score_threshold": -0.5,
            "momentum_weight": 0.4,
            "mean_reversion_weight": 0.3,
            "volume_weight": 0.2,
            "volatility_weight": 0.1,
        },
        parameter_validator=_validate_factor_based,
        parameter_schema=FactorBasedParameters,
        warmup_bars_fn=_warmup_factor_based,
        required_indicators=("momentum", "z_score", "rolling_standard_deviation", "volume_ratio"),
        required_persisted_features=(),
        parameter_specs=(
            ParameterSpec(
                name="momentum_lookback",
                parameter_type=ParameterType.INT,
                default=5,
                description="Return lookback period for the momentum factor.",
                min_value=1,
                max_value=60,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
            ParameterSpec(
                name="mean_reversion_window",
                parameter_type=ParameterType.INT,
                default=20,
                description="Rolling window for the z-score mean-reversion factor.",
                min_value=5,
                max_value=100,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
            ParameterSpec(
                name="volatility_window",
                parameter_type=ParameterType.INT,
                default=20,
                description="Rolling window for the volatility factor.",
                min_value=5,
                max_value=100,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
            ParameterSpec(
                name="volume_window",
                parameter_type=ParameterType.INT,
                default=20,
                description="Rolling window for the volume-ratio factor.",
                min_value=5,
                max_value=100,
                discrete=True,
                step=1,
                tunable=True,
                mutation_strategy="step",
            ),
            ParameterSpec(
                name="buy_score_threshold",
                parameter_type=ParameterType.FLOAT,
                default=0.5,
                description="Composite score above which to emit a BUY signal.",
                min_value=0.1,
                max_value=1.0,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
            ParameterSpec(
                name="sell_score_threshold",
                parameter_type=ParameterType.FLOAT,
                default=-0.5,
                description="Composite score below which to emit a SELL signal.",
                min_value=-1.0,
                max_value=-0.1,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
            ParameterSpec(
                name="momentum_weight",
                parameter_type=ParameterType.FLOAT,
                default=0.4,
                description="Weight assigned to the momentum factor in composite scoring.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
            ParameterSpec(
                name="mean_reversion_weight",
                parameter_type=ParameterType.FLOAT,
                default=0.3,
                description="Weight assigned to the mean-reversion factor.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
            ParameterSpec(
                name="volume_weight",
                parameter_type=ParameterType.FLOAT,
                default=0.2,
                description="Weight assigned to the volume factor.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
            ParameterSpec(
                name="volatility_weight",
                parameter_type=ParameterType.FLOAT,
                default=0.1,
                description="Weight assigned to the volatility factor.",
                min_value=0.0,
                max_value=1.0,
                discrete=False,
                tunable=True,
                mutation_strategy="gaussian",
            ),
        ),
        builder=_build_factor_based,
        deterministic=True,
    )
)

# Seal the registry — no further registrations permitted at runtime.
_REGISTRY.lock()
