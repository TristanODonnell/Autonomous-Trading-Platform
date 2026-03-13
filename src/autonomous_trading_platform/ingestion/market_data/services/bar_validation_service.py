from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from autonomous_trading_platform.contracts.market.market_bar import MarketBar
from autonomous_trading_platform.contracts.validators.core import ValidationResult, run_rules
from autonomous_trading_platform.contracts.validators.market_bar import MARKET_BAR_RULES


class BarValidationService:
    def validate_bar(self, bar: MarketBar) -> ValidationResult:
        return run_rules(bar, MARKET_BAR_RULES)

    def is_late_bar(
        self,
        bar: MarketBar,
        now_utc: datetime,
        allowed_delay: timedelta,
    ) -> bool:
        return now_utc > (bar.end_timestamp + allowed_delay)

    def is_suspected_outlier(
        self,
        bar: MarketBar,
        reference_close: Decimal | None,
        max_move_pct: Decimal = Decimal("0.20"),
    ) -> bool:
        if reference_close is None or reference_close == 0:
            return False

        move_pct = abs((bar.close - reference_close) / reference_close)
        return move_pct > max_move_pct
