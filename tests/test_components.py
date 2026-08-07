"""PriceZone and Interconnector: typed stand-ins for zone names and links."""

import copy
import pickle

import pytest

from openeuphemia import BidCurve, Interconnector, PowerMarket, PriceZone


def test_price_zone_is_interchangeable_with_its_name():
    zone = PriceZone("NORD", country="IT")
    assert zone == "NORD"
    assert hash(zone) == hash("NORD")
    assert "NORD" in {zone}
    assert f"{zone}" == "NORD"


def test_price_zone_carries_attributes():
    zone = PriceZone("NORD", country="IT", tso="Terna")
    assert zone.country == "IT"
    assert zone.attributes == {"country": "IT", "tso": "Terna"}
    assert repr(zone) == "PriceZone('NORD', country='IT', tso='Terna')"


def test_price_zone_attributes_survive_copy_and_pickle():
    # A plain str subclass loses __dict__ on pickling; PriceZone must not.
    zone = PriceZone("NORD", country="IT")
    assert copy.copy(zone).country == "IT"
    assert pickle.loads(pickle.dumps(zone)).country == "IT"


def test_price_zone_clears_like_a_string():
    nord, sud = PriceZone("NORD"), PriceZone("SUD")
    market = PowerMarket(zones=[nord, sud], interconnectors=[(nord, sud)])
    market.add_bid_curve(
        zone=nord, period=1,
        supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
    )
    market.set_ntc(nord, sud, capacity_mwh=500.0)
    prices = market.clear(method="per-period-lp").prices
    assert prices["zone"].tolist() == ["NORD", "SUD"]


def test_interconnector_unpacks_like_a_pair():
    link = Interconnector("A", "B")
    assert tuple(link) == ("A", "B")
    from_zone, to_zone = link
    assert (from_zone, to_zone) == ("A", "B")


def test_interconnector_capacity_becomes_the_ntc():
    market = PowerMarket(
        zones=["A", "B"],
        interconnectors=[Interconnector("A", "B", capacity_mwh=500.0)],
    )
    market.add_bid_curve(
        zone="A", period=1,
        supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
    )
    market.validate()
    link = market.interconnectors.iloc[0]
    assert (link["min_flow_mwh"], link["max_flow_mwh"]) == (-500.0, 500.0)


def test_interconnector_directional_capacity_and_name():
    market = PowerMarket(
        zones=["A", "B"],
        interconnectors=[
            Interconnector(
                "A", "B",
                forward_capacity_mwh=300.0, reverse_capacity_mwh=100.0,
                kind="hvdc", name="A_B_DC",
            )
        ],
    )
    market.add_bid_curve(
        zone="A", period=1,
        supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
    )
    market.validate()
    link = market.interconnectors.iloc[0]
    assert link["id"] == "A_B_DC"
    assert (link["min_flow_mwh"], link["max_flow_mwh"]) == (-100.0, 300.0)


def test_set_ntc_overrides_a_declared_capacity_for_one_interval():
    market = PowerMarket(
        zones=["A", "B"],
        interconnectors=[Interconnector("A", "B", capacity_mwh=500.0)],
    )
    for period in (1, 2):
        market.add_bid_curve(
            zone="A", period=period,
            supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
        )
    market.set_ntc("A", "B", capacity_mwh=10.0, period=2)
    market.validate()
    limits = market.interconnectors.set_index("period")["max_flow_mwh"]
    assert limits[1] == 500.0
    assert limits[2] == 10.0


def test_interconnector_without_capacity_leaves_the_link_unconstrained():
    market = PowerMarket(zones=["A", "B"], interconnectors=[Interconnector("A", "B")])
    market.add_bid_curve(
        zone="A", period=1,
        supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
    )
    market.validate()
    assert market.interconnectors.iloc[0]["max_flow_mwh"] == float("inf")


def test_interconnector_rejects_bad_kind_and_conflicting_capacities():
    with pytest.raises(ValueError, match="kind must be"):
        Interconnector("A", "B", kind="dc")
    with pytest.raises(ValueError, match="not both"):
        Interconnector("A", "B", capacity_mwh=1.0, forward_capacity_mwh=2.0)


def test_plain_strings_and_tuples_still_work():
    market = PowerMarket(zones=["A", "B"], interconnectors=[("A", "B")])
    market.add_bid_curve(
        zone="A", period=1,
        supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
    )
    market.set_ntc("A", "B", capacity_mwh=50.0)
    market.validate()
    assert market.interconnectors.iloc[0]["id"] == "A-B"
