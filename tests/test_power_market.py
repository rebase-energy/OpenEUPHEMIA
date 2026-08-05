"""Declaring topology and boundary conditions on a PowerMarket."""

import pandas as pd
import pytest

from openeuphemia import BidCurve, PowerMarket


def test_interconnectors_can_be_declared_as_pairs_then_set_with_ntc():
    market = PowerMarket(
        name="two-zone",
        delivery_day="2025-04-01",
        zones=["A", "B"],
        interconnectors=[("A", "B")],
        periods=[1],
    )
    assert market.has_interconnector("A", "B")
    assert market.has_interconnector("B", "A")  # undirected
    market.set_ntc("A", "B", capacity_mwh=500.0)
    market.validate()
    link = market.interconnectors.iloc[0]
    assert (link["from_zone"], link["to_zone"]) == ("A", "B")
    assert (link["min_flow_mwh"], link["max_flow_mwh"]) == (-500.0, 500.0)


def test_set_ntc_swaps_directional_capacities_for_the_reverse_orientation():
    market = PowerMarket(
        name="two-zone",
        delivery_day="2025-04-01",
        zones=["A", "B"],
        interconnectors=[("A", "B")],
        periods=[1],
    )
    # Declared A -> B, but the capacity is set from B's perspective.
    market.set_ntc("B", "A", forward_capacity_mwh=10.0, reverse_capacity_mwh=20.0)
    market.validate()
    link = market.interconnectors.iloc[0]
    assert (link["min_flow_mwh"], link["max_flow_mwh"]) == (-10.0, 20.0)


def test_set_ntc_on_an_undeclared_link_is_rejected():
    market = PowerMarket(
        name="two-zone",
        delivery_day="2025-04-01",
        zones=["A", "B", "C"],
        interconnectors=[("A", "B")],
        periods=[1],
    )
    with pytest.raises(ValueError, match="no interconnector"):
        market.set_ntc("B", "C", capacity_mwh=10.0)


def test_interconnector_pairs_reject_unknown_zones_and_self_loops():
    with pytest.raises(ValueError, match="unknown zone"):
        PowerMarket(
            name="m", delivery_day="2025-04-01", zones=["A", "B"],
            interconnectors=[("A", "C")],
        )
    with pytest.raises(ValueError, match="itself"):
        PowerMarket(
            name="m", delivery_day="2025-04-01", zones=["A"],
            interconnectors=[("A", "A")],
        )


def test_add_bid_curve_rejects_a_zone_outside_the_declared_zones():
    market = PowerMarket(name="m", delivery_day="2025-04-01", zones=["A"], periods=[1])
    with pytest.raises(ValueError, match="unknown zone"):
        market.add_bid_curve(zone="Z", period=1, supply=BidCurve([(10.0, 5.0)]))


def test_price_and_flow_boundaries_use_the_renamed_methods():
    market = PowerMarket(name="m", delivery_day="2025-04-01", zones=["A"], periods=[1])
    market.add_price_boundary(
        id="ext_a", period=1, zone="A", price_eur_per_mwh=30.0,
        import_capacity_mwh=100.0, export_capacity_mwh=100.0,
    )
    market.add_flow_boundary(id="fixed_a", period=1, zone="A", quantity_mwh=10.0)
    assert list(market.boundary_prices["price_eur_per_mwh"]) == [30.0]
    assert list(market.boundary_flows["quantity_mwh"]) == [10.0]


def test_a_materialized_interconnector_table_still_works_without_declared_pairs():
    # The raw-DataFrame path (e.g. built by another tool) bypasses set_ntc
    # entirely and needs no topology declaration.
    market = PowerMarket(
        name="m",
        delivery_day="2025-04-01",
        zones=["A", "B"],
        periods=[1],
        interconnectors=pd.DataFrame(
            [{"id": "A_B", "period": 1, "from_zone": "A", "to_zone": "B",
              "min_flow_mwh": -50.0, "max_flow_mwh": 50.0}]
        ),
    )
    assert not market.has_interconnector("A", "B")
    market.validate()
    assert len(market.interconnectors) == 1
