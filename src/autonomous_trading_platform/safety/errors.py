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
