"""Built-in component registrations for the canonical ComponentRegistry."""

from __future__ import annotations

from autonomous_trading_platform.strategy.indicators.mean_reversion import (
    distance_from_moving_average,
    z_score,
)
from autonomous_trading_platform.strategy.indicators.momentum import (
    momentum,
    rate_of_change,
    rsi,
    rsi_wilder,
)
from autonomous_trading_platform.strategy.indicators.trend import (
    exponential_moving_average,
    simple_moving_average,
)
from autonomous_trading_platform.strategy.indicators.volatility import (
    realized_volatility,
    rolling_standard_deviation,
)
from autonomous_trading_platform.strategy.indicators.volume import (
    average_volume,
    volume_ratio,
    volume_spike,
)
from autonomous_trading_platform.strategy.signal_logic.aggregation import (
    LogicalAggregator,
    VotingAggregator,
    WeightedScoreAggregator,
)
from autonomous_trading_platform.strategy.signal_logic.comparison_rule import ComparisonRule
from autonomous_trading_platform.strategy.signal_logic.crossover_rule import CrossoverRule
from autonomous_trading_platform.strategy.signal_logic.threshold_rule import ThresholdRule

from .component_definition import ComponentDefinition
from .component_parameter_schemas import float_parameter, int_parameter
from .component_registry import _COMPONENT_REGISTRY
from .component_type import ComponentType

PRICE_INPUTS = {"values": "list[float]"}
VOLUME_INPUTS = {"volumes": "list[float]"}

WINDOW = int_parameter(
    "window",
    default=20,
    description="Rolling lookback window in bars.",
    min_value=1,
    max_value=250,
)
LOOKBACK = int_parameter(
    "lookback",
    default=5,
    description="Lookback offset in bars.",
    min_value=1,
    max_value=250,
)
CONFIDENCE = float_parameter(
    "confidence",
    default=0.55,
    description="Signal confidence emitted when the rule triggers.",
    min_value=0.0,
    max_value=1.0,
)


def _register_indicator(
    *,
    name: str,
    display_name: str,
    description: str,
    implementation,
    required_inputs: tuple[str, ...],
    input_types: dict[str, str],
    parameter_specs,
    required_bar_fields: tuple[str, ...],
    warmup_parameter: str | None,
    warmup_formula: str,
    output_type: str = "float",
    output_domain: str | None = None,
) -> None:
    _COMPONENT_REGISTRY.register(
        ComponentDefinition(
            component_name=name,
            component_type=ComponentType.INDICATOR,
            display_name=display_name,
            description=description,
            implementation=implementation,
            required_inputs=required_inputs,
            input_types=input_types,
            required_bar_fields=required_bar_fields,
            parameter_specs=tuple(parameter_specs),
            warmup_parameter=warmup_parameter,
            warmup_formula=warmup_formula,
            deterministic=True,
            output_type=output_type,
            output_domain=output_domain,
            compatible_component_types=(ComponentType.SIGNAL_RULE, ComponentType.AGGREGATOR),
            production_ready=True,
        )
    )


_register_indicator(
    name="simple_moving_average",
    display_name="SMA",
    description="Simple moving average over the trailing price window.",
    implementation=simple_moving_average,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(WINDOW,),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="exponential_moving_average",
    display_name="EMA",
    description="Exponential moving average seeded from the first full window.",
    implementation=exponential_moving_average,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(WINDOW,),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="momentum",
    display_name="Momentum",
    description="Absolute price delta from the configured lookback bar.",
    implementation=momentum,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(LOOKBACK,),
    required_bar_fields=("close",),
    warmup_parameter="lookback",
    warmup_formula="lookback + 1",
)
_register_indicator(
    name="rate_of_change",
    display_name="Rate of Change",
    description="Percent return from the configured lookback bar.",
    implementation=rate_of_change,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(LOOKBACK,),
    required_bar_fields=("close",),
    warmup_parameter="lookback",
    warmup_formula="lookback + 1",
    output_domain="percent_return",
)
_register_indicator(
    name="rsi",
    display_name="Cutler RSI",
    description="Cutler RSI using simple averages of gains and losses.",
    implementation=rsi,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(
        int_parameter("window", default=14, description="RSI period.", min_value=2, max_value=100),
    ),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="window + 1",
    output_domain="0_100",
)
_register_indicator(
    name="rsi_wilder",
    display_name="Wilder RSI",
    description="Wilder RSI using exponential smoothing after the seed period.",
    implementation=rsi_wilder,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(
        int_parameter("window", default=14, description="RSI period.", min_value=2, max_value=100),
    ),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="2 * window + 1",
    output_domain="0_100",
)
_register_indicator(
    name="z_score",
    display_name="Z-Score",
    description="Distance from the rolling mean in rolling standard deviations.",
    implementation=z_score,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(WINDOW,),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="distance_from_moving_average",
    display_name="Distance from Moving Average",
    description="Absolute difference between the latest price and its trailing SMA.",
    implementation=distance_from_moving_average,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(WINDOW,),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="rolling_standard_deviation",
    display_name="Rolling Standard Deviation",
    description="Sample standard deviation over the trailing price window.",
    implementation=rolling_standard_deviation,
    required_inputs=("values",),
    input_types=PRICE_INPUTS,
    parameter_specs=(WINDOW,),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="realized_volatility",
    display_name="Realized Volatility",
    description="Rolling standard deviation over return inputs.",
    implementation=realized_volatility,
    required_inputs=("returns",),
    input_types={"returns": "list[float]"},
    parameter_specs=(WINDOW,),
    required_bar_fields=("close",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="average_volume",
    display_name="Average Volume",
    description="Simple moving average of volume over the trailing window.",
    implementation=average_volume,
    required_inputs=("volumes",),
    input_types=VOLUME_INPUTS,
    parameter_specs=(WINDOW,),
    required_bar_fields=("volume",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="volume_ratio",
    display_name="Volume Ratio",
    description="Current volume divided by trailing average volume.",
    implementation=volume_ratio,
    required_inputs=("volumes",),
    input_types=VOLUME_INPUTS,
    parameter_specs=(WINDOW,),
    required_bar_fields=("volume",),
    warmup_parameter="window",
    warmup_formula="window",
)
_register_indicator(
    name="volume_spike",
    display_name="Volume Spike",
    description="Boolean volume-spike flag when volume ratio exceeds a threshold.",
    implementation=volume_spike,
    required_inputs=("volumes",),
    input_types=VOLUME_INPUTS,
    parameter_specs=(
        WINDOW,
        float_parameter(
            "threshold",
            default=2.0,
            description="Minimum volume ratio treated as a spike.",
            min_value=1.0,
            max_value=10.0,
        ),
    ),
    required_bar_fields=("volume",),
    warmup_parameter="window",
    warmup_formula="window",
    output_type="bool",
    output_domain="boolean",
)

_COMPONENT_REGISTRY.register(
    ComponentDefinition(
        component_name="threshold",
        component_type=ComponentType.SIGNAL_RULE,
        display_name="Threshold",
        description="Maps one numeric value to BUY/SELL using strict threshold comparisons.",
        implementation=ThresholdRule,
        required_inputs=("value",),
        input_types={"value": "float"},
        parameter_specs=(
            float_parameter(
                "buy_above",
                default=0.0,
                description="Strict BUY-above threshold.",
                min_value=-10.0,
                max_value=10.0,
            ),
            float_parameter(
                "buy_below",
                default=0.0,
                description="Strict BUY-below threshold.",
                min_value=-10.0,
                max_value=10.0,
            ),
            float_parameter(
                "sell_above",
                default=0.0,
                description="Strict SELL-above threshold.",
                min_value=-10.0,
                max_value=10.0,
            ),
            float_parameter(
                "sell_below",
                default=0.0,
                description="Strict SELL-below threshold.",
                min_value=-10.0,
                max_value=10.0,
            ),
            CONFIDENCE,
        ),
        constraints=("Equality does not trigger a signal.",),
        output_type="SignalRuleResult",
        output_domain="buy_sell_none",
        compatible_component_types=(ComponentType.AGGREGATOR,),
    )
)
_COMPONENT_REGISTRY.register(
    ComponentDefinition(
        component_name="crossover",
        component_type=ComponentType.SIGNAL_RULE,
        display_name="Crossover",
        description="Emits BUY when fast crosses above slow and SELL when fast crosses below slow.",
        implementation=CrossoverRule,
        required_inputs=("previous_fast", "previous_slow", "current_fast", "current_slow"),
        input_types={
            "previous_fast": "float",
            "previous_slow": "float",
            "current_fast": "float",
            "current_slow": "float",
        },
        parameter_specs=(CONFIDENCE,),
        constraints=(
            "Previous equality can participate in a crossover; current equality does not trigger.",
        ),
        warmup_bars=2,
        output_type="SignalRuleResult",
        output_domain="buy_sell_none",
        compatible_component_types=(ComponentType.AGGREGATOR,),
    )
)
_COMPONENT_REGISTRY.register(
    ComponentDefinition(
        component_name="comparison",
        component_type=ComponentType.SIGNAL_RULE,
        display_name="Comparison",
        description="Compares two numeric inputs and maps above/below outcomes to signal sides.",
        implementation=ComparisonRule,
        required_inputs=("left_value", "right_value"),
        input_types={"left_value": "float", "right_value": "float"},
        parameter_specs=(CONFIDENCE,),
        constraints=("Equality does not trigger a signal.",),
        output_type="SignalRuleResult",
        output_domain="buy_sell_none",
        compatible_component_types=(ComponentType.AGGREGATOR,),
    )
)

for name, display_name, implementation, description, parameter_specs, constraints in (
    (
        "voting",
        "Voting",
        VotingAggregator,
        "Chooses the majority signal side once the side reaches the minimum vote count.",
        (
            int_parameter(
                "min_votes",
                default=2,
                description="Minimum same-side votes required.",
                min_value=1,
                max_value=25,
            ),
        ),
        ("Input order is deterministic but voting is count-based.",),
    ),
    (
        "weighted_score",
        "Weighted Score",
        WeightedScoreAggregator,
        "Combines signal directions and confidences into a normalized weighted score.",
        (
            float_parameter(
                "buy_threshold",
                default=0.6,
                description="Minimum normalized score for BUY.",
                min_value=0.0,
                max_value=1.0,
            ),
            float_parameter(
                "sell_threshold",
                default=-0.6,
                description="Maximum normalized score for SELL.",
                min_value=-1.0,
                max_value=0.0,
            ),
        ),
        (
            "Weighted inputs must use positive weights.",
            "Input order must be stable for reproducible diagnostics.",
        ),
    ),
    (
        "logical_and",
        "Logical AND",
        LogicalAggregator,
        "Requires every input rule to match the configured side.",
        (),
        ("Requires at least one input.", "Aggregation target direction is constructor metadata."),
    ),
    (
        "logical_or",
        "Logical OR",
        LogicalAggregator,
        "Requires at least one input rule to match the configured side.",
        (),
        ("Aggregation target direction is constructor metadata.",),
    ),
):
    _COMPONENT_REGISTRY.register(
        ComponentDefinition(
            component_name=name,
            component_type=ComponentType.AGGREGATOR,
            display_name=display_name,
            description=description,
            implementation=implementation,
            required_inputs=("SignalRuleResult",),
            input_types={"results": "list[SignalRuleResult]"},
            parameter_specs=parameter_specs,
            constraints=constraints,
            output_type="SignalRuleResult",
            output_domain="buy_sell_none",
            compatible_component_types=(ComponentType.SIGNAL_RULE,),
        )
    )

for component_type, names in (
    (ComponentType.FILTER, ("volatility", "liquidity", "regime", "time")),
    (ComponentType.EXIT_RULE, ("fixed", "trailing", "signal_based")),
    (ComponentType.SIZING, ("fixed", "volatility_adjusted", "confidence_weighted")),
):
    for name in names:
        _COMPONENT_REGISTRY.register(
            ComponentDefinition(
                component_name=f"{name}_{component_type.value}",
                component_type=component_type,
                display_name=name.replace("_", " ").title(),
                description=(
                    f"Metadata placeholder for future {name.replace('_', ' ')} "
                    f"{component_type.value.replace('_', ' ')} component."
                ),
                implementation=None,
                is_executable=False,
                metadata_only=True,
                required_inputs=(),
                input_types={},
                parameter_specs=(),
                deterministic=True,
                output_type=component_type.value,
                production_ready=False,
                experimental=True,
            )
        )

_COMPONENT_REGISTRY.lock()
