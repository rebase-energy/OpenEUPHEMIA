"""HiGHS-backed solver for SDK component-table markets."""

from __future__ import annotations

from dataclasses import dataclass
from math import isclose
from typing import Any, Mapping

import pandas as pd

from openeuphemia.core import DEMAND, Market, MarketClearingResult
from openeuphemia.exceptions import InfeasibleMarketError, SolverUnavailableError

TOLERANCE = 1e-7


@dataclass
class _BuiltMarketModel:
    highs: Any
    order_vars: dict[str, Any]
    block_vars: dict[str, Any]
    flow_vars: dict[tuple[str, int], Any]
    boundary_price_vars: dict[tuple[str, int], Any]
    balance_constraints: dict[tuple[int, str], Any]
    market: Market


def solve_component_market(
    market: Market,
    *,
    solver: str = "auto",
    include_accepted_orders: bool = True,
) -> MarketClearingResult:
    """Clear a generic component-table market."""

    normalized = solver.lower()
    if normalized not in {"auto", "highs", "highspy"}:
        raise ValueError("component-table markets currently support solver='highs' only")
    _require_highspy()
    market.validate()

    milp = _build_model(market)
    _run_highs(milp.highs)
    objective_value = float(milp.highs.getObjectiveValue())
    fixed_blocks = {
        block_id: 1 if float(milp.highs.val(var)) >= 0.5 else 0
        for block_id, var in milp.block_vars.items()
    }

    lp = _build_model(market, fixed_block_values=fixed_blocks)
    _run_highs(lp.highs)

    prices = pd.DataFrame(
        [
            {
                "period": period,
                "zone": zone,
                "price_eur_per_mwh": float(lp.highs.constrDual(constraint)),
            }
            for (period, zone), constraint in sorted(lp.balance_constraints.items())
        ],
        columns=["period", "zone", "price_eur_per_mwh"],
    )
    flows = _flow_results(lp)
    accepted_orders = (
        _accepted_order_results(lp) if include_accepted_orders else pd.DataFrame()
    )
    return MarketClearingResult(
        delivery_day=market.delivery_day,
        status="optimal",
        objective_value=objective_value,
        solver="highspy",
        prices=prices,
        flows=flows,
        accepted_orders=accepted_orders,
        metadata={"market": market.name, "backend": "component-table"},
    )


def _build_model(
    market: Market,
    *,
    fixed_block_values: Mapping[str, int] | None = None,
) -> _BuiltMarketModel:
    import highspy

    fixed_block_values = dict(fixed_block_values or {})
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)

    objective_terms: list[tuple[Any, float]] = []
    order_vars = {}
    for row in market.orders.itertuples(index=False):
        variable = h.addVariable(lb=0.0, ub=row.quantity_mwh, name=f"order_{row.id}")
        order_vars[row.id] = variable
        objective_terms.append(
            (variable, _welfare_coefficient(row.side, row.price_eur_per_mwh))
        )
    block_ids = sorted(set(market.block_orders["block_id"])) if not market.block_orders.empty else []
    block_objective_coefficients = _block_objective_coefficients(market)
    block_vars = {}
    for block_id in block_ids:
        variable = _block_activation_var(h, block_id, fixed_block_values)
        block_vars[block_id] = variable
        coefficient = block_objective_coefficients.get(block_id, 0.0)
        if not isclose(coefficient, 0.0, abs_tol=TOLERANCE):
            objective_terms.append((variable, coefficient))
    flow_vars = {
        (row.id, int(row.period)): h.addVariable(
            lb=float(row.min_flow_mwh),
            ub=float(row.max_flow_mwh),
            name=f"flow_{row.id}_{int(row.period)}",
        )
        for row in market.interconnectors.itertuples(index=False)
    }
    boundary_price_vars = {}
    for row in market.boundary_prices.itertuples(index=False):
        variable = h.addVariable(
            lb=-float(row.import_capacity_mwh),
            ub=float(row.export_capacity_mwh),
            name=f"boundary_price_{row.id}_{int(row.period)}",
        )
        boundary_price_vars[(str(row.id), int(row.period))] = variable
        objective_terms.append((variable, float(row.price_eur_per_mwh)))

    _add_block_parent_constraints(h, market, block_vars)
    balance_constraints = _add_balance_constraints(
        h,
        market,
        order_vars,
        block_vars,
        flow_vars,
        boundary_price_vars,
    )
    _add_flow_based_constraints(h, market, flow_vars, boundary_price_vars)

    _maximize_linear_objective(h, objective_terms)
    return _BuiltMarketModel(
        highs=h,
        order_vars=order_vars,
        block_vars=block_vars,
        flow_vars=flow_vars,
        boundary_price_vars=boundary_price_vars,
        balance_constraints=balance_constraints,
        market=market,
    )


def _block_activation_var(
    h: Any,
    block_id: str,
    fixed_block_values: Mapping[str, int],
) -> Any:
    import highspy

    if block_id in fixed_block_values:
        value = float(fixed_block_values[block_id])
        return h.addVariable(lb=value, ub=value, name=f"block_{block_id}")
    return h.addVariable(
        lb=0.0,
        ub=1.0,
        type=highspy.HighsVarType.kInteger,
        name=f"block_{block_id}",
    )


def _add_block_parent_constraints(
    h: Any,
    market: Market,
    block_vars: Mapping[str, Any],
) -> None:
    if market.block_orders.empty or "parent_block_id" not in market.block_orders.columns:
        return
    parents = (
        market.block_orders[["block_id", "parent_block_id"]]
        .dropna()
        .drop_duplicates()
    )
    for row in parents.itertuples(index=False):
        block_id = str(row.block_id)
        parent_id = str(row.parent_block_id)
        if parent_id not in block_vars:
            raise ValueError(f"block {block_id!r} references unknown parent {parent_id!r}")
        h.addConstr(
            block_vars[block_id] <= block_vars[parent_id],
            name=f"link_{parent_id}_{block_id}",
        )


def _add_balance_constraints(
    h: Any,
    market: Market,
    order_vars: Mapping[str, Any],
    block_vars: Mapping[str, Any],
    flow_vars: Mapping[tuple[str, int], Any],
    boundary_price_vars: Mapping[tuple[str, int], Any],
) -> dict[tuple[int, str], Any]:
    boundary = _boundary_by_period_zone(market.boundary_flows)
    constraints: dict[tuple[int, str], Any] = {}
    for period in market.periods["period"]:
        for zone in market.zones["zone"]:
            expr = None
            has_variable = False
            for row in market.orders[
                (market.orders["period"] == period) & (market.orders["zone"] == zone)
            ].itertuples(index=False):
                sign = _balance_sign(row.side)
                expr = _add_expr(expr, sign * order_vars[row.id])
                has_variable = True
            for row in market.block_orders[
                (market.block_orders["period"] == period)
                & (market.block_orders["zone"] == zone)
            ].itertuples(index=False):
                sign = _balance_sign(row.side)
                expr = _add_expr(
                    expr,
                    sign * float(row.quantity_mwh) * block_vars[row.block_id],
                )
                has_variable = True
            for row in market.interconnectors[
                market.interconnectors["period"] == period
            ].itertuples(index=False):
                variable = flow_vars[(row.id, int(row.period))]
                if row.from_zone == zone:
                    expr = _add_expr(expr, variable)
                    has_variable = True
                if row.to_zone == zone:
                    expr = _add_expr(expr, -variable)
                    has_variable = True
            for row in market.boundary_prices[
                (market.boundary_prices["period"] == period)
                & (market.boundary_prices["zone"] == zone)
            ].itertuples(index=False):
                variable = boundary_price_vars[(str(row.id), int(row.period))]
                expr = _add_expr(expr, variable)
                has_variable = True
            constant = boundary.get((int(period), str(zone)), 0.0)
            if has_variable:
                expr = _add_expr(expr, constant)
                constraints[(int(period), str(zone))] = h.addConstr(
                    expr == 0.0,
                    name=f"balance_{int(period)}_{zone}",
                )
            elif not isclose(constant, 0.0, abs_tol=TOLERANCE):
                raise InfeasibleMarketError(
                    f"period {period} zone {zone} has boundary flow but no variables"
                )
    return constraints


def _flow_results(model: _BuiltMarketModel) -> pd.DataFrame:
    rows = []
    for row in model.market.interconnectors.itertuples(index=False):
        variable = model.flow_vars[(row.id, int(row.period))]
        rows.append(
            {
                "id": row.id,
                "period": int(row.period),
                "from_zone": row.from_zone,
                "to_zone": row.to_zone,
                "flow_mwh": float(model.highs.val(variable)),
                "flow_type": "interconnector",
            }
        )
    for row in model.market.boundary_flows.itertuples(index=False):
        rows.append(
            {
                "id": row.id,
                "period": int(row.period),
                "from_zone": row.zone,
                "to_zone": row.external_zone,
                "flow_mwh": float(row.quantity_mwh),
                "flow_type": "fixed_boundary",
            }
        )
    for row in model.market.boundary_prices.itertuples(index=False):
        variable = model.boundary_price_vars[(str(row.id), int(row.period))]
        rows.append(
            {
                "id": row.id,
                "period": int(row.period),
                "from_zone": row.zone,
                "to_zone": row.external_zone,
                "flow_mwh": float(model.highs.val(variable)),
                "flow_type": "price_boundary",
            }
        )
    return pd.DataFrame(
        rows,
        columns=["id", "period", "from_zone", "to_zone", "flow_mwh", "flow_type"],
    )


def _accepted_order_results(model: _BuiltMarketModel) -> pd.DataFrame:
    rows = []
    for row in model.market.orders.itertuples(index=False):
        rows.append(
            {
                "id": row.id,
                "period": int(row.period),
                "zone": row.zone,
                "side": row.side,
                "order_type": "simple",
                "price_eur_per_mwh": float(row.price_eur_per_mwh),
                "accepted_mwh": float(model.highs.val(model.order_vars[row.id])),
            }
        )
    for row in model.market.block_orders.itertuples(index=False):
        ratio = float(model.highs.val(model.block_vars[row.block_id]))
        rows.append(
            {
                "id": row.id,
                "block_id": row.block_id,
                "period": int(row.period),
                "zone": row.zone,
                "side": row.side,
                "order_type": "block",
                "price_eur_per_mwh": float(row.price_eur_per_mwh),
                "accepted_mwh": ratio * float(row.quantity_mwh),
            }
        )
    return pd.DataFrame(rows)


def _add_flow_based_constraints(
    h: Any,
    market: Market,
    flow_vars: Mapping[tuple[str, int], Any],
    boundary_price_vars: Mapping[tuple[str, int], Any],
) -> None:
    """Add published network constraints: sum of PTDF-weighted net positions <= RAM.

    Zone terms apply the coefficient to the zone's net export, expressed
    through its interconnector flows, price-boundary variables, and fixed
    boundary constants (which shift the right-hand side). Interconnector and
    boundary_price terms apply the coefficient directly to that variable.
    """

    constraints = getattr(market, "flow_based_constraints", None)
    if constraints is None or constraints.empty:
        return
    boundary = _boundary_by_period_zone(market.boundary_flows)
    links_by_zone: dict[str, list[tuple[str, int, float]]] = {}
    for row in market.interconnectors.itertuples(index=False):
        links_by_zone.setdefault(str(row.from_zone), []).append(
            (str(row.id), int(row.period), 1.0)
        )
        links_by_zone.setdefault(str(row.to_zone), []).append(
            (str(row.id), int(row.period), -1.0)
        )
    boundaries_by_zone: dict[str, list[tuple[str, int]]] = {}
    for row in market.boundary_prices.itertuples(index=False):
        boundaries_by_zone.setdefault(str(row.zone), []).append(
            (str(row.id), int(row.period))
        )

    for (period, constraint_id), group in constraints.groupby(
        ["period", "constraint_id"], sort=False
    ):
        period = int(period)
        expr = None
        rhs = float(group["ram_mwh"].iloc[0])
        for row in group.itertuples(index=False):
            coefficient = float(row.coefficient)
            if row.term_type == "zone":
                zone = str(row.ref)
                for link_id, link_period, sign in links_by_zone.get(zone, ()):
                    if link_period == period:
                        expr = _add_expr(
                            expr,
                            coefficient * sign * flow_vars[(link_id, link_period)],
                        )
                for boundary_id, boundary_period in boundaries_by_zone.get(zone, ()):
                    if boundary_period == period:
                        expr = _add_expr(
                            expr,
                            coefficient
                            * boundary_price_vars[(boundary_id, boundary_period)],
                        )
                rhs -= coefficient * boundary.get((period, zone), 0.0)
            elif row.term_type == "interconnector":
                variable = flow_vars.get((str(row.ref), period))
                if variable is not None:
                    expr = _add_expr(expr, coefficient * variable)
            elif row.term_type == "boundary_price":
                variable = boundary_price_vars.get((str(row.ref), period))
                if variable is not None:
                    expr = _add_expr(expr, coefficient * variable)
            else:
                raise ValueError(
                    f"unknown flow-based term_type {row.term_type!r}"
                )
        if expr is not None:
            h.addConstr(
                expr <= rhs,
                name=f"flow_based_{constraint_id}",
            )


def _boundary_by_period_zone(boundary_flows: pd.DataFrame) -> dict[tuple[int, str], float]:
    if boundary_flows.empty:
        return {}
    grouped = boundary_flows.groupby(["period", "zone"], as_index=False)["quantity_mwh"].sum()
    return {
        (int(row.period), str(row.zone)): float(row.quantity_mwh)
        for row in grouped.itertuples(index=False)
    }


def _balance_sign(side: str) -> float:
    return 1.0 if side == DEMAND else -1.0


def _welfare_coefficient(side: str, price: float) -> float:
    return float(price) if side == DEMAND else -float(price)


def _block_objective_coefficients(market: Market) -> dict[str, float]:
    if market.block_orders.empty:
        return {}
    coefficients: dict[str, float] = {}
    for row in market.block_orders.itertuples(index=False):
        coefficients[str(row.block_id)] = coefficients.get(str(row.block_id), 0.0) + (
            _welfare_coefficient(row.side, row.price_eur_per_mwh)
            * float(row.quantity_mwh)
        )
    return coefficients


def _maximize_linear_objective(h: Any, terms: list[tuple[Any, float]]) -> None:
    if not terms:
        h.run()
        return
    from highspy.highs import highs_linear_expression

    expression = highs_linear_expression()
    expression.idxs = [int(variable) for variable, _ in terms]
    expression.vals = [float(coefficient) for _, coefficient in terms]
    expression.constant = 0.0
    h.maximize(expression)


def _run_highs(h: Any) -> None:
    status = h.modelStatusToString(h.getModelStatus())
    if "Optimal" not in status:
        raise InfeasibleMarketError(f"HiGHS did not find an optimal solution: {status}")


def _require_highspy() -> None:
    try:
        import highspy  # noqa: F401
    except ImportError as exc:
        raise SolverUnavailableError("highspy is not installed") from exc


def _add_expr(expression: Any | None, term: Any) -> Any:
    if expression is None:
        return term
    if isinstance(term, float | int) and isclose(float(term), 0.0, abs_tol=TOLERANCE):
        return expression
    return expression + term
