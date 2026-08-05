"""Choosing among welfare-equal optima: which flows does the market pick?

Welfare maximization pins down the zonal prices uniquely, but not the
flows. Whenever two zones settle at the same price, a whole face of the
feasible polytope is optimal: many different exchange patterns support
the identical, maximal welfare. The LP solver returns an arbitrary vertex
of that face, while the market operator published a specific point on it.

Closing that gap needs a *selection rule* — a secondary objective applied
over the welfare optimum. This module implements the rules as extra
optimization stages, so prices are always read from stage one and are
never disturbed:

* ``"volume-max"`` — from EUPHEMIA's public description: among the
  welfare-optimal solutions, maximize the total accepted volume, so
  zero-surplus matches (supply and demand tied at the clearing price) are
  traded rather than left idle. Observation-free. It pins the traded
  volume but not how tied acceptance is split between zones.

* ``"pro-rata"`` — volume maximization, then share the remaining tied
  same-price acceptance in proportion to submitted volume. Implemented by
  minimizing the L1 deviation from a common acceptance ratio per (side,
  price) group within each uncongested cluster of zones. Observation-free,
  and it needs only per-zone at-price volumes, which aggregated curves
  contain. This rule is *not* in the public description — it was
  reverse-engineered from published outcomes; see the note on eras below.

* ``"anchored"`` — the two-stage tie-break against a reference schedule:
  restrict to the stage-one optimum (welfare held to within
  ``welfare_tolerance_eur``) and minimize the L1 distance of the link
  flows to ``anchor_flows``. This *consumes the published schedule*, so it
  is a replication device and an upper bound on what any selection rule
  can achieve — not a prediction.

Passing no selection at all leaves the solver free to return any optimal
vertex, which is the baseline the rules are measured against.

**Eras.** The pro-rata rule fits Italian outcomes from 2025 onward. In the
PUN era (before 2025) the published cross-zonal split instead follows the
description's merit-order priority rule, which requires per-offer
merit-order numbers and therefore an order book rather than aggregated
curves; it is not implemented here.
"""

from __future__ import annotations

from typing import Any, Mapping

import pandas as pd

from openeuphemia.core import Market, MarketClearingResult
from openeuphemia.exceptions import InfeasibleMarketError
from openeuphemia.solver.market import (
    _accepted_order_results,
    _build_model,
    _flow_results,
    _run_highs,
    _welfare_coefficient,
)
from openeuphemia.solver.per_period import _period_market

FLOW_SELECTION_METHODS = ("volume-max", "pro-rata", "anchored")
BLIND_FLOW_SELECTION_METHODS = ("volume-max", "pro-rata")

DEFAULT_WELFARE_TOLERANCE_EUR = 1e-3
DEFAULT_VOLUME_TOLERANCE_MWH = 1e-3


def clear_market_with_flow_selection(
    market: Market,
    *,
    method: str = "pro-rata",
    anchor_flows: Mapping[tuple[str, int], float] | None = None,
    welfare_tolerance_eur: float = DEFAULT_WELFARE_TOLERANCE_EUR,
    volume_tolerance_mwh: float = DEFAULT_VOLUME_TOLERANCE_MWH,
    solver: str = "auto",
) -> MarketClearingResult:
    """Clear each period, then apply ``method`` to select among optimal flows.

    ``anchor_flows`` maps ``(interconnector id, period)`` to the reference
    flow used by the ``"anchored"`` method; links without an anchor stay
    free. It is ignored by the observation-free methods.
    """

    if method not in FLOW_SELECTION_METHODS:
        raise ValueError(
            f"method must be one of {sorted(FLOW_SELECTION_METHODS)}, got {method!r}"
        )
    if method == "anchored" and not anchor_flows:
        raise ValueError("the anchored selection requires anchor_flows")
    if solver.lower() not in {"auto", "highs", "highspy"}:
        raise ValueError("flow selection supports solver='auto', 'highs', or 'highspy'")
    market.validate()
    if not market.block_orders.empty:
        raise ValueError("flow selection does not support block orders")

    price_frames: list[pd.DataFrame] = []
    flow_frames: list[pd.DataFrame] = []
    accepted_frames: list[pd.DataFrame] = []
    objective_value = 0.0
    for period in sorted(int(value) for value in market.periods["period"]):
        result = _solve_period(
            _period_market(market, period),
            method=method,
            anchor_flows=anchor_flows or {},
            welfare_tolerance_eur=welfare_tolerance_eur,
            volume_tolerance_mwh=volume_tolerance_mwh,
        )
        objective_value += result["welfare"]
        price_frames.append(result["prices"])
        flow_frames.append(result["flows"])
        accepted_frames.append(result["accepted_orders"])

    flows = pd.concat(flow_frames, ignore_index=True)
    return MarketClearingResult(
        delivery_day=market.delivery_day,
        status="optimal",
        objective_value=objective_value,
        solver="highspy",
        prices=pd.concat(price_frames, ignore_index=True)
        .sort_values(["period", "zone"])
        .reset_index(drop=True),
        flows=flows.sort_values(["period", "id"]).reset_index(drop=True)
        if not flows.empty
        else flows,
        accepted_orders=pd.concat(accepted_frames, ignore_index=True),
        metadata={"market": market.name, "backend": f"flow-selection-{method}"},
    )


def _solve_period(
    period_market: Market,
    *,
    method: str,
    anchor_flows: Mapping[tuple[str, int], float],
    welfare_tolerance_eur: float,
    volume_tolerance_mwh: float,
) -> dict[str, Any]:
    from highspy.highs import highs_linear_expression

    period_market.validate()
    model = _build_model(period_market)
    _run_highs(model.highs)
    h = model.highs
    welfare = float(h.getObjectiveValue())
    stage_one_prices = _price_rows(model)
    welfare_terms = _welfare_terms(model)

    if method in BLIND_FLOW_SELECTION_METHODS:
        # These selections redistribute order acceptance only: freeze the
        # boundary exchange variables at their stage-one values so the later
        # stages cannot spend the welfare tolerance on moving border volumes.
        for variable in model.boundary_price_vars.values():
            h.addConstr(variable == float(h.val(variable)))
        _constrain_welfare(h, welfare_terms, welfare - float(welfare_tolerance_eur))

        volume_expression = _expression(list(model.order_vars.values()))
        h.maximize(volume_expression)
        _run_highs(h)

        if method == "pro-rata":
            volume = float(h.getObjectiveValue())
            h.addConstr(
                _expression(list(model.order_vars.values()))
                >= volume - float(volume_tolerance_mwh)
            )
            _minimize_pro_rata_deviation(model, period_market, stage_one_prices)
    else:
        anchored = [
            (model.flow_vars[(row.id, int(row.period))], float(anchor))
            for row in period_market.interconnectors.itertuples(index=False)
            if (anchor := anchor_flows.get((str(row.id), int(row.period)))) is not None
        ]
        deviations = []
        for flow_var, anchor in anchored:
            up = h.addVariable(lb=0.0)
            down = h.addVariable(lb=0.0)
            h.addConstr(flow_var - up + down == anchor)
            deviations.extend((up, down))
        _constrain_welfare(h, welfare_terms, welfare - float(welfare_tolerance_eur))
        h.minimize(_expression(deviations))
        _run_highs(h)

    return {
        "welfare": welfare,
        "prices": stage_one_prices,
        "flows": _flow_results(model),
        "accepted_orders": _accepted_order_results(model),
    }


def _minimize_pro_rata_deviation(
    model: Any,
    period_market: Market,
    stage_one_prices: pd.DataFrame,
) -> None:
    """Equalize acceptance ratios within each (side, price, cluster) group."""

    h = model.highs
    cluster_of = _face_range_clusters(model, period_market, stage_one_prices)
    orders = period_market.orders.assign(
        _cluster=lambda frame: frame["zone"].map(cluster_of)
    )
    deviations = []
    for _key, group in orders.groupby(["side", "price_eur_per_mwh", "_cluster"]):
        if len(group) < 2:
            continue
        ratio = h.addVariable(lb=0.0, ub=1.0)
        for row in group.itertuples(index=False):
            up = h.addVariable(lb=0.0)
            down = h.addVariable(lb=0.0)
            h.addConstr(
                model.order_vars[row.id]
                - float(row.quantity_mwh) * ratio
                - up
                + down
                == 0.0
            )
            deviations.extend((up, down))
    if not deviations:
        return
    h.minimize(_expression(deviations))
    _run_highs(h)


def _face_range_clusters(
    model: Any,
    period_market: Market,
    stage_one_prices: pd.DataFrame,
) -> dict[str, str]:
    """Zone -> cluster root, joining zones across genuinely open links.

    A link is open when the optimal face (welfare and volume constraints
    already active on the model) admits an interior flow on it — probed by
    minimizing and maximizing the link's flow over the face. This is robust
    both against degenerate vertices parked at a bound on an open link and
    against zero-spread congestion, where a link binds although its
    endpoint prices are equal; floor-price hours are the canonical case,
    and clustering on price spread alone would wrongly merge them.
    """

    h = model.highs
    parent = {str(zone): str(zone) for zone in period_market.zones["zone"]}

    def find(zone: str) -> str:
        while parent[zone] != zone:
            parent[zone] = parent[parent[zone]]
            zone = parent[zone]
        return zone

    price_of = {
        str(row.zone): float(row.price_eur_per_mwh)
        for row in stage_one_prices.itertuples(index=False)
    }
    tolerance = 1e-3
    for row in period_market.interconnectors.itertuples(index=False):
        variable = model.flow_vars[(row.id, int(row.period))]
        expression = _expression([variable])
        try:
            h.maximize(expression)
            _run_highs(h)
            flow_max = float(h.val(variable))
            h.minimize(expression)
            _run_highs(h)
            flow_min = float(h.val(variable))
            interior_possible = (
                flow_max > float(row.min_flow_mwh) + tolerance
                and flow_min < float(row.max_flow_mwh) - tolerance
            )
        except InfeasibleMarketError:
            # Numerical trouble in the probe: fall back to the price-spread
            # criterion for this link (open iff zero spread).
            spread = abs(
                price_of.get(str(row.from_zone), 0.0)
                - price_of.get(str(row.to_zone), 0.0)
            )
            interior_possible = spread < 1e-6
        if interior_possible:
            root_a, root_b = find(str(row.from_zone)), find(str(row.to_zone))
            if root_a != root_b:
                parent[root_a] = root_b
    return {zone: find(zone) for zone in parent}


def _expression(variables: list[Any], values: list[float] | None = None) -> Any:
    from highspy.highs import highs_linear_expression

    expression = highs_linear_expression()
    expression.idxs = [int(variable) for variable in variables]
    expression.vals = (
        [1.0] * len(variables) if values is None else [float(v) for v in values]
    )
    expression.constant = 0.0
    return expression


def _constrain_welfare(
    highs: Any,
    welfare_terms: list[tuple[Any, float]],
    lower_bound: float,
) -> None:
    expression = _expression(
        [variable for variable, _ in welfare_terms],
        [value for _, value in welfare_terms],
    )
    highs.addConstr(expression >= lower_bound)


def _welfare_terms(model: Any) -> list[tuple[Any, float]]:
    market = model.market
    terms = [
        (
            model.order_vars[row.id],
            _welfare_coefficient(row.side, row.price_eur_per_mwh),
        )
        for row in market.orders.itertuples(index=False)
    ]
    terms.extend(
        (
            model.boundary_price_vars[(str(row.id), int(row.period))],
            float(row.price_eur_per_mwh),
        )
        for row in market.boundary_prices.itertuples(index=False)
    )
    return terms


def _price_rows(model: Any) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "period": period,
                "zone": zone,
                "price_eur_per_mwh": float(model.highs.constrDual(constraint)),
            }
            for (period, zone), constraint in sorted(model.balance_constraints.items())
        ],
        columns=["period", "zone", "price_eur_per_mwh"],
    )
