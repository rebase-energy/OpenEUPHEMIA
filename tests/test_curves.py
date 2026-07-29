"""Cumulative bid curves and their construction from tidy tables."""

import pandas as pd
import pytest

from openeuphemia import BidCurve, bid_curves_from_table


def test_from_steps_orders_and_accumulates_supply():
    curve = BidCurve.from_steps([60.0, 50.0], [4.0, 10.0], side="supply")
    assert curve.prices == (50.0, 60.0)
    assert curve.cumulative_volumes == (10.0, 14.0)
    assert curve.volumes == (10.0, 4.0)
    assert curve.total_volume == 14.0


def test_from_steps_orders_demand_by_descending_price():
    curve = BidCurve.from_steps([80.0, 120.0], [6.0, 2.0], side="demand")
    assert curve.prices == (120.0, 80.0)
    assert curve.cumulative_volumes == (2.0, 8.0)


def test_from_steps_merges_quantities_at_the_same_price():
    curve = BidCurve.from_steps([50.0, 50.0, 60.0], [3.0, 7.0, 1.0], side="supply")
    assert curve.prices == (50.0, 60.0)
    assert curve.cumulative_volumes == (10.0, 11.0)


def test_from_steps_drops_empty_steps():
    curve = BidCurve.from_steps([50.0, 60.0], [10.0, 0.0], side="supply")
    assert curve.prices == (50.0,)


def test_from_steps_rejects_an_entirely_empty_curve():
    with pytest.raises(ValueError):
        BidCurve.from_steps([50.0], [0.0], side="supply")


def test_to_orders_lowers_the_curve_to_order_rows():
    curve = BidCurve.from_steps([50.0, 60.0], [10.0, 4.0], side="supply")
    orders = curve.to_orders(zone="NORD", period=1, side="supply")
    assert [row["quantity_mwh"] for row in orders] == [10.0, 4.0]
    assert [row["price_eur_per_mwh"] for row in orders] == [50.0, 60.0]
    assert orders[0]["id"] == "NORD_p1_supply_0"


def test_bid_curves_from_table_groups_by_period_zone_and_side():
    table = pd.DataFrame(
        [
            {"period": 1, "zone": "NORD", "side": "supply", "price_eur_per_mwh": 50.0, "quantity_mwh": 10.0},
            {"period": 1, "zone": "NORD", "side": "demand", "price_eur_per_mwh": 90.0, "quantity_mwh": 8.0},
            {"period": 2, "zone": "SUD", "side": "supply", "price_eur_per_mwh": 40.0, "quantity_mwh": 5.0},
        ]
    )
    curves = bid_curves_from_table(table)
    assert sorted(curves) == [(1, "NORD"), (2, "SUD")]
    assert sorted(curves[(1, "NORD")]) == ["demand", "supply"]
    assert curves[(2, "SUD")]["supply"].total_volume == 5.0


def test_bid_curves_from_table_requires_the_expected_columns():
    with pytest.raises(ValueError, match="missing columns"):
        bid_curves_from_table(pd.DataFrame([{"period": 1, "zone": "NORD"}]))
