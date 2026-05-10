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
