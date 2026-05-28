class SafetyError(RuntimeError):
    """Base error for safety-gate failures."""


class LiveTradingBlockedError(SafetyError):
    """Raised when live trading is blocked by one or more safety gates."""


class RuntimeGateNotArmedError(SafetyError):
    """Raised when live trading has not been manually armed at runtime."""


class KillSwitchEnabledError(SafetyError):
    """Raised when the external kill switch is enabled."""


class BuildGateDisabledError(SafetyError):
    """Raised when live modules are excluded from the build."""


class ConfigGateDisabledError(SafetyError):
    """Raised when config does not explicitly allow live trading."""


class GrossExposureLimitExceededError(SafetyError):
    """Raised when a proposed order would exceed the configured gross exposure limit."""


class SymbolExposureLimitExceededError(SafetyError):
    """Raised when a proposed order would exceed the configured per-symbol exposure limit."""


class DailyNotionalLimitExceededError(SafetyError):
    """Raised when a proposed order would exceed the configured daily notional traded limit."""


class OrdersPerHourLimitExceededError(SafetyError):
    """Raised when the maximum number of orders per hour has been reached."""


class OrdersPerBarLimitExceededError(SafetyError):
    """Raised when the maximum number of orders per bar has been reached."""


class RepeatedOrderInBarError(SafetyError):
    """Raised when an order is repeated for the same symbol/side within the same bar."""


class DuplicateIdempotencyKeyError(SafetyError):
    """Raised when an order is submitted with an idempotency key that already exists."""


class PortfolioSymbolExposureLimitExceededError(SafetyError):
    """Raised when a proposed order would push aggregate portfolio symbol exposure past configured limits."""

    def __init__(
        self,
        *,
        symbol: str,
        strategy_id: str,
        current_exposure_usd: float,
        projected_exposure_usd: float,
        limit_usd: float | None,
        current_exposure_pct: float,
        projected_exposure_pct: float,
        limit_pct: float | None,
    ) -> None:
        self.symbol = symbol
        self.strategy_id = strategy_id
        self.current_exposure_usd = current_exposure_usd
        self.projected_exposure_usd = projected_exposure_usd
        self.limit_usd = limit_usd
        self.current_exposure_pct = current_exposure_pct
        self.projected_exposure_pct = projected_exposure_pct
        self.limit_pct = limit_pct

        parts: list[str] = []
        if limit_usd is not None:
            parts.append(f"USD ${projected_exposure_usd:,.2f} > limit ${limit_usd:,.2f}")
        if limit_pct is not None:
            parts.append(f"pct {projected_exposure_pct:.2%} > limit {limit_pct:.2%}")
        detail = "; ".join(parts) or "limit exceeded"
        super().__init__(
            f"Portfolio symbol exposure limit exceeded for {symbol} "
            f"(strategy={strategy_id}): {detail}."
        )


class SectorConcentrationLimitExceededError(SafetyError):
    """Raised when a proposed order would push sector concentration past configured limits."""

    def __init__(
        self,
        *,
        symbol: str,
        sector: str,
        strategy_id: str,
        current_sector_exposure_pct: float,
        projected_sector_exposure_pct: float,
        configured_limit_pct: float,
        proposed_order_notional: float,
        run_id: str | None = None,
    ) -> None:
        self.symbol = symbol
        self.sector = sector
        self.strategy_id = strategy_id
        self.current_sector_exposure_pct = current_sector_exposure_pct
        self.projected_sector_exposure_pct = projected_sector_exposure_pct
        self.configured_limit_pct = configured_limit_pct
        self.proposed_order_notional = proposed_order_notional
        self.run_id = run_id
        super().__init__(
            f"Sector concentration limit exceeded for {sector!r} "
            f"(symbol={symbol}, strategy={strategy_id}): "
            f"projected {projected_sector_exposure_pct:.2%} > limit {configured_limit_pct:.2%}."
        )
