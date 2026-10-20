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


class BrokerOrderRejectedError(ExecutionError):
    """Raised when the broker refuses an order outright (HTTP 4xx on submit).

    The request was understood and turned down (insufficient buying power, no position
    to sell, bad parameters), so retrying cannot help and the rest of the tick's intents
    must still go out. Carries the broker's status code and response body, which is the
    only place the reason is stated.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: str,
        symbol: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body
        self.symbol = symbol
