"""Selection rules resolving which welfare-equal flow pattern is returned."""

import pandas as pd
import pytest

from openeuphemia.core import Market
from openeuphemia.solver.flow_selection import clear_market_with_flow_selection


def degenerate_market() -> Market:
    """Two zones that clear at the same price with tied, indeterminate supply.

    Each zone offers 100 MWh at 10 EUR and demands (40, 60) MWh at the cap.
    Total demand is 100 MWh against 200 MWh of identically priced supply, so
    welfare cannot say which zone's supply is accepted — every split of the
    100 accepted MWh is optimal, and each implies a different flow.
    """

    orders = pd.DataFrame(
        [
            {"id": "a_s", "period": 1, "zone": "A", "side": "supply", "price_eur_per_mwh": 10.0, "quantity_mwh": 100.0},
            {"id": "b_s", "period": 1, "zone": "B", "side": "supply", "price_eur_per_mwh": 10.0, "quantity_mwh": 100.0},
            {"id": "a_d", "period": 1, "zone": "A", "side": "demand", "price_eur_per_mwh": 4000.0, "quantity_mwh": 40.0},
            {"id": "b_d", "period": 1, "zone": "B", "side": "demand", "price_eur_per_mwh": 4000.0, "quantity_mwh": 60.0},
        ]
    )
    interconnectors = pd.DataFrame(
        [
            {
                "id": "A-B",
                "period": 1,
                "from_zone": "A",
                "to_zone": "B",
                "min_flow_mwh": -500.0,
                "max_flow_mwh": 500.0,
            }
        ]
    )
    return Market(
        name="degenerate",
        delivery_day="2025-04-01",
        zones=["A", "B"],
        periods=[1],
        orders=orders,
        interconnectors=interconnectors,
    )


def flow_of(result) -> float:
    row = result.flows[result.flows["flow_type"] == "interconnector"].iloc[0]
    return float(row["flow_mwh"])


def test_pro_rata_shares_tied_acceptance_by_submitted_volume():
    result = clear_market_with_flow_selection(degenerate_market(), method="pro-rata")
    # Equal submitted volumes -> each zone's supply accepted 50%, i.e. 50 MWh.
    # A consumes 40 and so exports 10; B consumes 60 and imports 10.
    assert flow_of(result) == pytest.approx(10.0, abs=1e-6)
    accepted = dict(zip(result.accepted_orders["id"], result.accepted_orders["accepted_mwh"]))
    assert accepted["a_s"] == pytest.approx(50.0, abs=1e-6)
    assert accepted["b_s"] == pytest.approx(50.0, abs=1e-6)


def test_pro_rata_shares_in_proportion_when_volumes_differ():
    market = degenerate_market()
    market.orders.loc[market.orders["id"] == "b_s", "quantity_mwh"] = 300.0
    result = clear_market_with_flow_selection(market, method="pro-rata")
    accepted = dict(zip(result.accepted_orders["id"], result.accepted_orders["accepted_mwh"]))
    # 100 MWh accepted across 100 + 300 submitted -> a quarter of each.
    assert accepted["a_s"] == pytest.approx(25.0, abs=1e-6)
    assert accepted["b_s"] == pytest.approx(75.0, abs=1e-6)


def test_anchored_selection_reproduces_the_anchor():
    result = clear_market_with_flow_selection(
        degenerate_market(),
        method="anchored",
        anchor_flows={("A-B", 1): -25.0},
    )
    assert flow_of(result) == pytest.approx(-25.0, abs=1e-6)


def test_selection_leaves_prices_untouched():
    baseline = degenerate_market().clear(method="per-period-lp")
    selected = clear_market_with_flow_selection(degenerate_market(), method="pro-rata")
    assert baseline.prices.set_index("zone")["price_eur_per_mwh"].to_dict() == pytest.approx(
        selected.prices.set_index("zone")["price_eur_per_mwh"].to_dict()
    )


def test_volume_maximization_accepts_zero_surplus_matches():
    # Supply and demand tied at 10 EUR: the match earns no surplus, so plain
    # welfare maximization is indifferent to trading it. Volume maximization
    # is not.
    orders = pd.DataFrame(
        [
            {"id": "s", "period": 1, "zone": "A", "side": "supply", "price_eur_per_mwh": 10.0, "quantity_mwh": 50.0},
            {"id": "d", "period": 1, "zone": "A", "side": "demand", "price_eur_per_mwh": 10.0, "quantity_mwh": 50.0},
        ]
    )
    market = Market(
        name="tied", delivery_day="2025-04-01", zones=["A"], periods=[1], orders=orders
    )
    result = clear_market_with_flow_selection(market, method="volume-max")
    accepted = dict(zip(result.accepted_orders["id"], result.accepted_orders["accepted_mwh"]))
    assert accepted["s"] == pytest.approx(50.0, abs=1e-6)
    assert accepted["d"] == pytest.approx(50.0, abs=1e-6)


def test_unknown_method_and_missing_anchor_are_rejected():
    with pytest.raises(ValueError, match="method must be one of"):
        clear_market_with_flow_selection(degenerate_market(), method="nope")
    with pytest.raises(ValueError, match="requires anchor_flows"):
        clear_market_with_flow_selection(degenerate_market(), method="anchored")


def test_market_clear_accepts_a_flow_selection():
    result = degenerate_market().clear(flow_selection="pro-rata")
    assert result.metadata["backend"] == "flow-selection-pro-rata"
    assert flow_of(result) == pytest.approx(10.0, abs=1e-6)
