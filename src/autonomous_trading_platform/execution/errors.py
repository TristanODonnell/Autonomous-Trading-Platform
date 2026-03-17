class ExecutionError(RuntimeError):
    """Base class for exceptions in this module."""


class InvalidOrderTransitionError(ExecutionError):
    pass
