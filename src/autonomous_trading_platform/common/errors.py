class InfrastructureError(RuntimeError):
    """Base class for infrastructure/dependency failures."""


class TransientInfrastructureError(InfrastructureError):
    """Retryable infrastructure or dependency failure."""


class PersistentInfrastructureError(InfrastructureError):
    """Non-retryable infrastructure failure."""


class BrokerServiceUnavailableError(TransientInfrastructureError):
    """Raised when the broker service is temporarily unavailable (timeouts, 5xx)."""


class DatabaseConnectionError(TransientInfrastructureError):
    """Raised when database connectivity temporarily fails."""


class ExternalServiceTimeoutError(TransientInfrastructureError):
    """Raised when an external dependency times out."""


class MisconfiguredInfrastructureError(PersistentInfrastructureError):
    """Raised when a dependency is misconfigured and cannot succeed without intervention."""
