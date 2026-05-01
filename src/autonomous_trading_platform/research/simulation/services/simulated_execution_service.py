import dataclasses
import uuid
from decimal import Decimal
from typing import Any

from autonomous_trading_platform.contracts.trading.fill import Fill
from autonomous_trading_platform.research.simulation.models.fill_model import (
    MarketFillPolicy,
    SimulatedFillModelConfig,
)
from autonomous_trading_platform.research.simulation.services.simulation_cost_model_service import (
    SimulationCostModelService,
)


class SimulatedExecutionService:
    def __init__(
        self,
        simulation_cost_model_service: SimulationCostModelService,
        fill_model_config: SimulatedFillModelConfig,
    ):
        self.simulation_cost_model_service = simulation_cost_model_service
        self.fill_model_config = fill_model_config

    @property
    def cost_model_summary(self) -> dict:
        return {
            k: str(v) if isinstance(v, Decimal) else v
            for k, v in dataclasses.asdict(self.simulation_cost_model_service.config).items()
        }

    @property
    def slippage_model_summary(self) -> dict:
        return {
            k: str(v) if isinstance(v, Decimal) else v
            for k, v in dataclasses.asdict(
                self.simulation_cost_model_service.slippage_model.config
            ).items()
        }

    def fill(
        self,
        *,
        order_intents: list[Any],
        bars_at_timestamp: dict[str, Any],
    ) -> list[Fill]:
        fills: list[Fill] = []

        for intent in order_intents:
            bar = bars_at_timestamp.get(intent.symbol)
            if bar is None:
                continue

            if intent.qty is None or intent.qty <= 0:
                continue

            fill = self._build_fill_for_intent(intent=intent, bar=bar)

            if fill is not None:
                fills.append(fill)

        return fills

    def _build_fill_for_intent(self, *, intent: Any, bar: Any) -> Fill | None:
        order_type = self._normalize_enum_value(intent.order_type)

        if order_type == "market":
            return self._fill_market_order(intent=intent, bar=bar)

        if order_type == "limit":
            return self._fill_limit_order(intent=intent, bar=bar)

        return None

    def _fill_market_order(self, *, intent: Any, bar: Any) -> Fill:
        if self.fill_model_config.market_fill_policy == MarketFillPolicy.CURRENT_CLOSE:
            return self._create_fill(
                intent=intent,
                bar=bar,
                reference_price=bar.close,
            )

        if self.fill_model_config.market_fill_policy == MarketFillPolicy.NEXT_OPEN:
            raise NotImplementedError("next_open market fill policy is deferred")

        raise ValueError(
            f"unsupported market_fill_policy={self.fill_model_config.market_fill_policy}"
        )

    def _fill_limit_order(self, *, intent: Any, bar: Any) -> Fill | None:
        limit_price = getattr(intent, "limit_price", None)

        if limit_price is None:
            return None

        limit_price_decimal = Decimal(str(limit_price))
        side = self._normalize_enum_value(intent.side)

        if side == "buy":
            crossed = Decimal(str(bar.low)) <= limit_price_decimal
        elif side == "sell":
            crossed = Decimal(str(bar.high)) >= limit_price_decimal
        else:
            return None

        if not crossed:
            return None

        return self._create_fill(
            intent=intent,
            bar=bar,
            reference_price=limit_price_decimal,
        )

    def _create_fill(
        self,
        *,
        intent: Any,
        bar: Any,
        reference_price: Any,
    ) -> Fill:
        costs = self.simulation_cost_model_service.apply_costs(
            side=intent.side,
            reference_price=reference_price,
            quantity=intent.qty,
        )

        return Fill(
            fill_id=str(uuid.uuid4()),
            broker_order_id=str(uuid.uuid4()),
            intent_id=intent.intent_id,
            run_id=intent.run_id,
            symbol=intent.symbol,
            timestamp=bar.timestamp,
            side=intent.side,
            quantity=intent.qty,
            price=costs.fill_price,
            fees=costs.commission,
            liquidity=None,
            venue="simulated",
        )

    def _normalize_enum_value(self, value: Any) -> str:
        raw_value = getattr(value, "value", value)
        return str(raw_value).lower()
