from __future__ import annotations


class ExecutionPolicyError(Exception):
    """Base class for execution policy errors."""


class MissingMarketDataError(ExecutionPolicyError):
    """
    Raised when a policy requires live market data (e.g. mid-price for LIMIT
    orders) but get_latest_quotes() returned nothing for the symbol.
    """


class UnsupportedPolicyModeError(ExecutionPolicyError):
    """Raised if an unknown PolicyMode is configured."""


class InvalidSliceConfigError(ExecutionPolicyError):
    """Raised if TWAP/VWAP-lite config parameters are invalid for the order."""
