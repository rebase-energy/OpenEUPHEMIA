"""Per-period LP clearing: fast prices-only clearing of large curve markets.

Each delivery period is cleared as an independent linear program and
zonal prices are read from the balance-constraint duals. This is much
faster than the joint multi-period MILP for large aggregated-curve
markets, at the cost of not returning per-order acceptance and only
supporting all-or-nothing blocks through a per-period MILP fallback.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from openeuphemia.core import Market, MarketClearingResult
from openeuphemia.solver.market import _build_model, _flow_results, _run_highs


def clear_market_per_period(
    market: Market,
    *,
    solver: str = "auto",
) -> MarketClearingResult:
    """Clear each period independently and return dual prices and flows."""

    normalized = solver.lower()
    if normalized not in {"auto", "highs", "highspy"}:
        raise ValueError(
            "per-period clearing supports solver='auto', 'highs', or 'highspy'"
        )

    price_frames = []
    flow_frames = []
    objective_value = 0.0
    market.validate()
    for period in list(market.periods["period"]):
        period_market = _period_market(market, int(period))
        period_market.validate()
        if not period_market.block_orders.empty:
            result = period_market.clear(solver=solver)
            objective_value += result.objective_value
            price_frames.append(result.prices)
            if not result.flows.empty:
                flow_frames.append(result.flows)
            continue

        model = _build_model(period_market)
        _run_highs(model.highs)
        objective_value += float(model.highs.getObjectiveValue())
        price_frames.append(
            pd.DataFrame(
                [
                    {
                        "period": constraint_period,
                        "zone": zone,
                        "price_eur_per_mwh": float(model.highs.constrDual(constraint)),
                    }
                    for (constraint_period, zone), constraint in sorted(
                        model.balance_constraints.items()
                    )
                ],
                columns=["period", "zone", "price_eur_per_mwh"],
            )
        )
        flows = _flow_results(model)
        if not flows.empty:
            flow_frames.append(flows)

    prices = _concat(price_frames, ["period", "zone", "price_eur_per_mwh"])
    flows = _concat(
        flow_frames,
        ["id", "period", "from_zone", "to_zone", "flow_mwh", "flow_type"],
    )
    return MarketClearingResult(
        delivery_day=market.delivery_day,
        status="optimal",
        objective_value=objective_value,
        solver="highspy",
        prices=prices.sort_values(["period", "zone"]).reset_index(drop=True),
        flows=flows.sort_values(["period", "id"]).reset_index(drop=True)
        if not flows.empty
        else flows,
        accepted_orders=pd.DataFrame(),
        metadata={"market": market.name, "backend": "period-price-only"},
    )


# Backwards-compatible name used by the research scripts.
clear_by_period_prices_only = clear_market_per_period


def _period_market(market: Market, period: int) -> Market:
    return Market(
        name=f"{market.name}-period-{period}",
        delivery_day=market.delivery_day,
        zones=market.zones.copy(),
        periods=[period],
        orders=_period_rows(market.orders, period),
        block_orders=_period_rows(market.block_orders, period),
        complex_orders=_period_rows(market.complex_orders, period),
        interconnectors=_period_rows(market.interconnectors, period),
        boundary_flows=_period_rows(market.boundary_flows, period),
        boundary_prices=_period_rows(market.boundary_prices, period),
        flow_based_constraints=_period_rows(
            getattr(market, "flow_based_constraints", pd.DataFrame()), period
        ),
        metadata=market.metadata,
    )


def _period_rows(frame: pd.DataFrame, period: int) -> pd.DataFrame:
    if frame.empty or "period" not in frame.columns:
        return frame.copy()
    return frame[frame["period"] == period].copy()


def _concat(frames: Sequence[pd.DataFrame], columns: Sequence[str]) -> pd.DataFrame:
    if not frames:
        return pd.DataFrame(columns=list(columns))
    return pd.concat(frames, ignore_index=True)
