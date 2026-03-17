class StrategyError(RuntimeError):
    """Base class for exceptions in this module."""


class InvalidStrategyTransitionError(StrategyError):
    """Raised when a strategy lifecycle event is invalid for the current state."""
