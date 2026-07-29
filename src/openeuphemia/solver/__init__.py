"""Market clearing solver entrypoints."""

from __future__ import annotations

from openeuphemia.core import Market, MarketClearingResult
from openeuphemia.solver.market import solve_component_market
from openeuphemia.solver.per_period import (
    clear_by_period_prices_only,
    clear_market_per_period,
)

CLEARING_METHODS = ("full-milp", "per-period-lp")

__all__ = [
    "CLEARING_METHODS",
    "clear_by_period_prices_only",
    "clear_market",
    "clear_market_per_period",
    "solve_component_market",
]


def clear_market(
    market: Market,
    *,
    solver: str = "auto",
    method: str = "full-milp",
    iterations_count: int = 150,
) -> MarketClearingResult:
    """Clear a component-table market.

    ``method`` selects the clearing formulation:

    - ``"full-milp"`` (default): one joint welfare-maximization MILP across
      all periods with all-or-nothing block orders, followed by an LP
      re-solve with fixed block decisions to obtain zonal prices from the
      duals.
    - ``"per-period-lp"``: each period cleared as an independent LP with
      prices from the balance-constraint duals. Much faster for large
      aggregated curve markets; returns no per-order acceptance, and periods
      containing block orders fall back to the MILP formulation.

    ``iterations_count`` is reserved for iterative clearing procedures
    (e.g. MIBEL complex conditions) and is currently unused.
    """

    normalized_method = method.lower().replace("_", "-")
    if normalized_method not in CLEARING_METHODS:
        raise ValueError(
            f"method must be one of {sorted(CLEARING_METHODS)}, got {method!r}"
        )
    if normalized_method == "per-period-lp":
        return clear_market_per_period(market, solver=solver)
    return solve_component_market(market, solver=solver)
