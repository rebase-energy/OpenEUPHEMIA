"""Domain-specific exceptions for market clearing."""


class OpenEuphemiaError(Exception):
    """Base exception for package errors."""


class InfeasibleMarketError(OpenEuphemiaError):
    """Raised when a market problem has no feasible clearing."""


class SolverUnavailableError(OpenEuphemiaError):
    """Raised when the requested optimization backend is not installed."""
