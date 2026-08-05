"""Clearing a small two-zone market: prices from duals, congestion rent."""

import pandas as pd

from openeuphemia.core import PowerMarket
from openeuphemia.solver import clear_market


def two_zone_market(max_flow: float) -> PowerMarket:
    orders = pd.DataFrame(
        [
            {"id": "a_s", "period": 1, "zone": "A", "side": "supply", "price_eur_per_mwh": 10.0, "quantity_mwh": 100.0},
            {"id": "a_d", "period": 1, "zone": "A", "side": "demand", "price_eur_per_mwh": 90.0, "quantity_mwh": 40.0},
            {"id": "b_s", "period": 1, "zone": "B", "side": "supply", "price_eur_per_mwh": 50.0, "quantity_mwh": 100.0},
            {"id": "b_d", "period": 1, "zone": "B", "side": "demand", "price_eur_per_mwh": 90.0, "quantity_mwh": 60.0},
        ]
    )
    interconnectors = pd.DataFrame(
        [
            {
                "id": "A_B",
                "period": 1,
                "from_zone": "A",
                "to_zone": "B",
                "min_flow_mwh": -max_flow,
                "max_flow_mwh": max_flow,
            }
        ]
    )
    return PowerMarket(
        name="two-zone",
        delivery_day="2025-04-01",
        zones=["A", "B"],
        periods=[1],
        orders=orders,
        interconnectors=interconnectors,
    )


def test_uncongested_market_has_one_price():
    result = clear_market(two_zone_market(1000.0), method="per-period-lp")
    prices = {
        row.zone: row.price_eur_per_mwh
        for row in result.prices.itertuples(index=False)
    }
    assert prices["A"] == prices["B"]
    # The cheap zone-A supply (100 MWh) covers total demand (100 MWh), so
    # zone-A supply is marginal at 10.
    assert prices["A"] == 10.0


def test_congested_market_splits_prices():
    result = clear_market(two_zone_market(20.0), method="per-period-lp")
    prices = {
        row.zone: row.price_eur_per_mwh
        for row in result.prices.itertuples(index=False)
    }
    # Only 20 MWh can move to B, so B's own 50-EUR supply becomes marginal.
    assert prices["A"] == 10.0
    assert prices["B"] == 50.0
    flow = result.flows.iloc[0]
    assert flow["flow_mwh"] == 20.0


def test_price_taking_boundary_sets_zone_price():
    market = two_zone_market(1000.0)
    market.boundary_prices = pd.DataFrame(
        [
            {
                "id": "ext_B",
                "period": 1,
                "zone": "B",
                "external_zone": "X",
                "price_eur_per_mwh": 30.0,
                "import_capacity_mwh": 500.0,
                "export_capacity_mwh": 500.0,
            }
        ]
    )
    result = clear_market(market, method="per-period-lp")
    prices = {
        row.zone: row.price_eur_per_mwh
        for row in result.prices.itertuples(index=False)
    }
    # The external price-taker is marginal: with cheap zone-A supply
    # exhausted below 30 EUR and an uncongested internal link, both zones
    # settle at the boundary price.
    assert prices["B"] == 30.0
    assert prices["A"] == 30.0
