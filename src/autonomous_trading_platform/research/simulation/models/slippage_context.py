from dataclasses import dataclass
from decimal import Decimal


@dataclass(slots=True)
class SlippageContext:
    """Bar and order context passed to slippage models for market-aware cost computation."""

    quantity: Decimal
    bar_volume: Decimal | None = None
    bar_high: Decimal | None = None
    bar_low: Decimal | None = None
    bar_close: Decimal | None = None
