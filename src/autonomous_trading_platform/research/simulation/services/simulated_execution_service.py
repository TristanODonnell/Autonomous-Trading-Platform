import uuid
from typing import Any

from autonomous_trading_platform.contracts.trading.fill import Fill


class SimulatedExecutionService:
    def fill(
        self,
        *,
        order_intents: list[Any],
        bars_at_timestamp: dict[str, Any],
    ) -> list[Fill]:
        fills = []
        for intent in order_intents:
            bar = bars_at_timestamp.get(intent.symbol)
            if bar is None:
                continue

            if intent.qty is None:
                continue

            fills.append(
                Fill(
                    fill_id=str(uuid.uuid4()),
                    broker_order_id=str(uuid.uuid4()),
                    intent_id=intent.intent_id,
                    run_id=intent.run_id,
                    symbol=intent.symbol,
                    timestamp=bar.timestamp,
                    side=intent.side,
                    quantity=intent.qty,
                    price=bar.close,
                    fees=None,
                    liquidity=None,
                    venue="simulated",
                )
            )
        return fills
