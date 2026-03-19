class ExecutionError(RuntimeError):
    """Base class for execution-domain failures."""


class InvalidOrderTransitionError(ExecutionError):
    """Raised when an order lifecycle event is invalid for the current state."""


class InvalidStrategyTransitionError(ExecutionError):
    """Raised when a strategy lifecycle event is invalid for the current state."""
