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
