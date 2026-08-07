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


def test_periods_are_inferred_from_the_data():
    market = PowerMarket(zones=["A"])
    for period in (1, 2, 3):
        market.add_bid_curve(
            zone="A", period=period,
            supply=BidCurve([(100.0, 10.0 * period)]),
            demand=BidCurve([(50.0, 4000.0)]),
        )
    market.validate()
    assert market.periods["period"].tolist() == [1, 2, 3]


def test_delivery_day_is_an_optional_label():
    # It plays no part in the optimization; it is carried to the result.
    market = PowerMarket(zones=["A"])
    market.add_bid_curve(
        zone="A", period=1,
        supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
    )
    assert market.delivery_day == ""
    assert market.clear(method="per-period-lp").delivery_day == ""

    labelled = PowerMarket(zones=["A"], delivery_day="2025-04-01")
    assert labelled.delivery_day == "2025-04-01"


def test_timestamps_accept_strings_datetimes_and_pandas_timestamps():
    import datetime as dt

    market = PowerMarket(zones=["A"])
    given = [
        pd.Timestamp("2025-04-01T02:00+02:00"),          # added first...
        "2025-04-01T00:00+02:00",                        # ...but earliest
        dt.datetime(2025, 4, 1, 1, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))),
    ]
    for timestamp in given:
        market.add_bid_curve(
            zone="A", timestamp=timestamp,
            supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
        )
    result = market.clear(method="per-period-lp")

    # ordinals follow chronology, not insertion order
    assert result.prices["period"].tolist() == [1, 2, 3]
    assert result.prices["timestamp"].tolist() == [
        pd.Timestamp("2025-04-01T00:00+02:00"),
        pd.Timestamp("2025-04-01T01:00+02:00"),
        pd.Timestamp("2025-04-01T02:00+02:00"),
    ]
    assert market.timestamps[1] == pd.Timestamp("2025-04-01T00:00+02:00")


def test_cleared_flows_carry_timestamps():
    market = PowerMarket(zones=["A", "B"], interconnectors=[("A", "B")])
    for zone in ("A", "B"):
        market.add_bid_curve(
            zone=zone, timestamp="2025-04-01T00:00+02:00",
            supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
        )
    market.set_ntc("A", "B", capacity_mwh=500.0)
    flows = market.clear(method="per-period-lp").flows
    assert flows["timestamp"].tolist() == [pd.Timestamp("2025-04-01T00:00+02:00")]


def test_set_ntc_can_be_scoped_by_timestamp():
    market = PowerMarket(zones=["A", "B"], interconnectors=[("A", "B")])
    for hour in (0, 1):
        for zone in ("A", "B"):
            market.add_bid_curve(
                zone=zone, timestamp=f"2025-04-01T0{hour}:00+02:00",
                supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
            )
    market.set_ntc("A", "B", capacity_mwh=500.0)                             # default
    market.set_ntc("A", "B", capacity_mwh=10.0,
                   timestamp="2025-04-01T01:00+02:00")                       # override
    market.validate()
    limits = market.interconnectors.set_index("period")["max_flow_mwh"]
    assert limits[1] == 500.0
    assert limits[2] == 10.0


def test_period_and_timestamp_are_mutually_exclusive():
    market = PowerMarket(zones=["A"])
    with pytest.raises(ValueError, match="exactly one"):
        market.add_bid_curve(zone="A", supply=BidCurve([(1.0, 1.0)]))
    with pytest.raises(ValueError, match="exactly one"):
        market.add_bid_curve(
            zone="A", period=1, timestamp="2025-04-01", supply=BidCurve([(1.0, 1.0)])
        )


def test_mixing_aware_and_naive_timestamps_is_rejected():
    market = PowerMarket(zones=["A"])
    market.add_bid_curve(zone="A", timestamp="2025-04-01T00:00+02:00",
                         supply=BidCurve([(1.0, 1.0)]))
    market.add_bid_curve(zone="A", timestamp="2025-04-01T01:00",
                         supply=BidCurve([(1.0, 1.0)]))
    with pytest.raises(ValueError, match="timezone-aware and timezone-naive"):
        market.validate()


def test_integer_periods_still_produce_no_timestamp_column():
    market = PowerMarket(zones=["A"], delivery_day="2025-04-01")
    market.add_bid_curve(zone="A", period=1,
                         supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]))
    result = market.clear(method="per-period-lp")
    assert "timestamp" not in result.prices.columns
    assert market.timestamps == {}


def test_add_bid_curve_rejects_a_zone_outside_the_declared_zones():
    market = PowerMarket(name="m", delivery_day="2025-04-01", zones=["A"], periods=[1])
    with pytest.raises(ValueError, match="unknown zone"):
        market.add_bid_curve(zone="Z", period=1, supply=BidCurve([(10.0, 5.0)]))


def test_price_and_flow_boundaries_are_separate_methods():
    market = PowerMarket(name="m", delivery_day="2025-04-01", zones=["A"], periods=[1])
    market.add_fixed_price_boundary(
        id="ext_a", period=1, zone="A", price_eur_per_mwh=30.0,
        import_capacity_mwh=100.0, export_capacity_mwh=100.0,
    )
    market.add_fixed_flow_boundary(id="fixed_a", period=1, zone="A", quantity_mwh=10.0)
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
