class ExecutionError(RuntimeError):
    """Base class for execution-domain failures."""


class InvalidOrderTransitionError(ExecutionError):
    """Raised when an order lifecycle event is invalid for the current state."""


class InvalidStrategyTransitionError(ExecutionError):
    """Raised when a strategy lifecycle event is invalid for the current state."""


class OrderNotAllowedForSubmissionError(ExecutionError):
    """Raised when an order submission is blocked by pre-trade controls."""


class BrokerStartupHealthCheckError(ExecutionError):
    """Raised when broker startup health checks fail closed."""


class InvalidBrokerCredentialsError(BrokerStartupHealthCheckError):
    """Raised when broker credentials are rejected by the broker."""


class BrokerRuntimeSyncError(ExecutionError):
    """Raised when broker runtime synchronization fails closed."""


class BrokerOrderNotFoundError(ExecutionError):
    """Raised when broker returns 404 for a known broker_order_id during reconciliation."""
