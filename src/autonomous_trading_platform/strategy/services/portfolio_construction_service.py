from autonomous_trading_platform.contracts.trading.signal import Signal


class PortfolioConstructionService:
    def __init__(self, risk_service) -> None:
        self.risk_service = risk_service

    def generate_order_intents(
        self,
        signals: list[Signal],
        positions: dict[str, int],
    ):
        target_positions = self.position_sizer(signals)
        deltas = self.calculate_deltas(positions, target_positions)

        for delta in deltas:
            order_intent = self.build_order_intent(delta)
            self.risk_service.assert_allowed(order_intent)
            yield order_intent

    def position_sizer(
        self,
        signals: list[Signal],
    ) -> dict[str, int]:
        target_positions: dict[str, int] = {}

        for signal in signals:
            # placeholder sizing rule
            target_qty = 10

            if signal.direction.value == "long":
                target_positions[signal.symbol] = target_qty
            elif signal.direction.value == "short":
                target_positions[signal.symbol] = -target_qty
            else:
                target_positions[signal.symbol] = 0

        return target_positions

    def calculate_deltas(
        self,
        current_positions: dict[str, int],
        target_positions: dict[str, int],
    ):
        deltas = []

        all_symbols = set(current_positions) | set(target_positions)

        for symbol in all_symbols:
            current_qty = current_positions.get(symbol, 0)
            target_qty = target_positions.get(symbol, 0)
            delta_qty = target_qty - current_qty

            if delta_qty != 0:
                deltas.append(
                    {
                        "symbol": symbol,
                        "current_qty": current_qty,
                        "target_qty": target_qty,
                        "delta_qty": delta_qty,
                    }
                )

        return deltas

    def build_order_intent(self, delta):
        side = "buy" if delta["delta_qty"] > 0 else "sell"

        return {
            "symbol": delta["symbol"],
            "side": side,
            "qty": abs(delta["delta_qty"]),
        }
