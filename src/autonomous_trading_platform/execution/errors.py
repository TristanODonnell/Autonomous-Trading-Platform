class ExecutionError(RuntimeError):
    """Base class for exceptions in this module."""


class InvalidOrderTransitionError(ExecutionError):
    pass


class InvalidStrategyTransitionError(ExecutionError):
    """Raised when a strategy lifecycle event is invalid for the current state."""
