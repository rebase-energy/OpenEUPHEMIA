"""PriceZone and Interconnector: typed stand-ins for zone names and links."""

import copy
import pickle

import pytest

from openeuphemia import (
    BidCurve,
    ExternalZone,
    Interconnector,
    PowerMarket,
    PriceZone,
)


def two_zone_market_with_border():
    """NORD/SUD internally, plus a border to the external zone FRAN."""
    nord, sud, fran = PriceZone("NORD"), PriceZone("SUD"), ExternalZone("FRAN")
    market = PowerMarket(
        zones=[nord, sud],
        interconnectors=[
            Interconnector(nord, sud, capacity_mwh=500.0),
            Interconnector(
                nord, fran, import_capacity_mwh=1000.0, export_capacity_mwh=1000.0
            ),
        ],
    )
    market.add_bid_curve(
        zone=nord, period=1,
        supply=BidCurve([(100.0, 10.0), (200.0, 80.0)]),
        demand=BidCurve([(40.0, 4000.0), (120.0, 30.0)]),
    )
    return market, nord, sud, fran


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


def test_external_zone_is_still_its_name():
    fran = ExternalZone("FRAN", country="FR")
    assert fran == "FRAN"
    assert isinstance(fran, PriceZone)
    assert fran.country == "FR"
    assert pickle.loads(pickle.dumps(fran)).country == "FR"
    assert repr(fran) == "ExternalZone('FRAN', country='FR')"


def test_a_border_does_not_become_an_internal_zone_or_link():
    market, _nord, _sud, _fran = two_zone_market_with_border()
    market.validate()
    # FRAN is reached across a boundary, so it is neither a zone nor a link.
    assert market.zones["zone"].tolist() == ["NORD", "SUD"]
    assert market.interconnectors["id"].tolist() == ["NORD-SUD"]


def test_boundary_takes_its_capacities_from_the_declared_border():
    market, nord, _sud, fran = two_zone_market_with_border()
    border = Interconnector(
        nord, fran, import_capacity_mwh=1000.0, export_capacity_mwh=1000.0
    )
    market.add_fixed_price_boundary(
        interconnector=border, period=1, price_eur_per_mwh=60.0
    )
    row = market.boundary_prices.iloc[0]
    assert row["id"] == "NORD_FRAN"          # derived from the two zones
    assert row["zone"] == "NORD"
    assert row["external_zone"] == "FRAN"
    assert (row["import_capacity_mwh"], row["export_capacity_mwh"]) == (1000.0, 1000.0)

    prices = market.clear(method="per-period-lp").prices
    assert prices.set_index("zone")["price_eur_per_mwh"].to_dict() == {
        "NORD": 60.0, "SUD": 60.0
    }


def test_capacities_passed_to_the_call_override_the_declared_ones():
    market, nord, _sud, fran = two_zone_market_with_border()
    market.add_fixed_price_boundary(
        interconnector=Interconnector(nord, fran, capacity_mwh=1000.0),
        period=1,
        price_eur_per_mwh=60.0,
        export_capacity_mwh=25.0,      # this interval only
    )
    row = market.boundary_prices.iloc[0]
    assert (row["import_capacity_mwh"], row["export_capacity_mwh"]) == (1000.0, 25.0)


def test_boundary_capacities_follow_the_internal_zone_whichever_way_declared():
    nord, fran = PriceZone("NORD"), ExternalZone("FRAN")
    # forward/reverse are relative to from_zone, so the mapping must flip
    # when the external zone is declared first.
    outward = Interconnector(nord, fran, forward_capacity_mwh=300.0, reverse_capacity_mwh=100.0)
    inward = Interconnector(fran, nord, forward_capacity_mwh=100.0, reverse_capacity_mwh=300.0)
    assert outward.boundary_capacities() == (100.0, 300.0)   # (import, export)
    assert inward.boundary_capacities() == (100.0, 300.0)
    assert outward.internal_zone == inward.internal_zone == "NORD"
    assert outward.external_zone == inward.external_zone == "FRAN"


def test_fixed_flow_boundary_also_accepts_a_declared_border():
    market, nord, _sud, fran = two_zone_market_with_border()
    market.add_fixed_flow_boundary(
        interconnector=Interconnector(nord, fran), period=1, quantity_mwh=42.0
    )
    row = market.boundary_flows.iloc[0]
    assert row["id"] == "NORD_FRAN"
    assert row["quantity_mwh"] == 42.0


def test_external_zones_cannot_carry_orders_or_stand_in_for_the_modelled_side():
    market, _nord, _sud, fran = two_zone_market_with_border()
    with pytest.raises(ValueError, match="outside the modelled area"):
        market.add_bid_curve(zone=fran, period=1, supply=BidCurve([(1.0, 1.0)]))
    with pytest.raises(ValueError, match="must be the modelled side"):
        market.add_fixed_price_boundary(
            zone=fran, external_zone="NORD", period=1, price_eur_per_mwh=1.0,
            import_capacity_mwh=1.0, export_capacity_mwh=1.0,
        )


def test_an_internal_link_is_rejected_as_a_boundary():
    market, nord, sud, _fran = two_zone_market_with_border()
    with pytest.raises(ValueError, match="no ExternalZone endpoint"):
        market.add_fixed_price_boundary(
            interconnector=Interconnector(nord, sud), period=1, price_eur_per_mwh=1.0
        )


def test_import_export_capacities_require_a_border():
    with pytest.raises(ValueError, match="describe a border"):
        Interconnector("A", "B", import_capacity_mwh=1.0)
    with pytest.raises(ValueError, match="not both"):
        Interconnector(
            "A", ExternalZone("X"), forward_capacity_mwh=1.0, import_capacity_mwh=1.0
        )


def test_explicit_zone_and_external_zone_still_work_without_declared_borders():
    # The dataframe-driven path builds boundaries per interval with their own
    # capacities and never declares an Interconnector.
    market = PowerMarket(zones=["A"])
    market.add_bid_curve(
        zone="A", period=1,
        supply=BidCurve([(100.0, 10.0)]), demand=BidCurve([(50.0, 4000.0)]),
    )
    market.add_fixed_price_boundary(
        id="A_X", period=1, zone="A", external_zone="X",
        price_eur_per_mwh=30.0, import_capacity_mwh=500.0, export_capacity_mwh=500.0,
    )
    assert market.clear(method="per-period-lp").prices["price_eur_per_mwh"].tolist() == [30.0]
