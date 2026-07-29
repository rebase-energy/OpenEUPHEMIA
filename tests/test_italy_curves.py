"""Zonal curve aggregation and block-order fixing from public offer rows."""

import pandas as pd

from openeuphemia.italy.curves import (
    BLOCK_FIXING_DEMAND_PRICE_EUR,
    BLOCK_FIXING_SUPPLY_PRICE_EUR,
    bid_curves_from_offers,
    orders_from_zonal_curves,
    split_block_offer_fixings,
    zonal_curves_from_offers,
)

DAY = "2025-04-01"


def offer_row(**overrides):
    row = {
        "delivery_date": DAY,
        "interval_no": 1,
        "zone_cd": "NORD",
        "purpose_cd": "OFF",
        "status_cd": "ACC",
        "energy_price_eur_per_mwh": 50.0,
        "adj_quantity_mw": 10.0,
        "awarded_quantity_mw": 10.0,
        "offer_type": "S",
    }
    row.update(overrides)
    return row


def test_simple_offers_aggregate_per_price_level():
    offers = pd.DataFrame(
        [
            offer_row(),
            offer_row(adj_quantity_mw=5.0),
            offer_row(energy_price_eur_per_mwh=60.0),
            offer_row(purpose_cd="BID", energy_price_eur_per_mwh=100.0),
            # Rejected offers stay in the curve (they define its shape).
            offer_row(status_cd="REJ", energy_price_eur_per_mwh=70.0),
            # Non-ACC/REJ statuses (e.g. revoked) are excluded.
            offer_row(status_cd="REV", energy_price_eur_per_mwh=80.0),
            # Other zones are excluded.
            offer_row(zone_cd="XFRA"),
        ]
    )
    curves, block_rows = zonal_curves_from_offers(offers, delivery_day=DAY, zones=("NORD",))
    assert block_rows == 0
    supply = curves[curves["side"] == "supply"]
    assert list(supply["price_eur_per_mwh"]) == [50.0, 60.0, 70.0]
    assert list(supply["quantity_mwh"]) == [15.0, 10.0, 10.0]
    demand = curves[curves["side"] == "demand"]
    assert list(demand["price_eur_per_mwh"]) == [100.0]


def test_accepted_blocks_become_price_taking_rows():
    offers = pd.DataFrame(
        [
            offer_row(),
            offer_row(
                offer_type="B",
                status_cd="ACC",
                energy_price_eur_per_mwh=90.0,
                awarded_quantity_mw=7.0,
            ),
            offer_row(
                offer_type="B",
                purpose_cd="BID",
                status_cd="ACC",
                energy_price_eur_per_mwh=1.0,
                awarded_quantity_mw=3.0,
            ),
            # Rejected blocks (including paradoxically rejected) are dropped.
            offer_row(offer_type="B", status_cd="REJ", awarded_quantity_mw=0.0),
        ]
    )
    remaining, fixings = split_block_offer_fixings(offers, delivery_day=DAY, zones=("NORD",))
    assert (remaining["offer_type"] == "S").all()
    by_side = {row.side: row for row in fixings.itertuples(index=False)}
    assert by_side["supply"].quantity_mwh == 7.0
    assert by_side["supply"].price_eur_per_mwh == BLOCK_FIXING_SUPPLY_PRICE_EUR
    assert by_side["demand"].quantity_mwh == 3.0
    assert by_side["demand"].price_eur_per_mwh == BLOCK_FIXING_DEMAND_PRICE_EUR


def test_bid_curves_are_monotone_and_cumulative():
    offers = pd.DataFrame(
        [
            offer_row(energy_price_eur_per_mwh=60.0, adj_quantity_mw=4.0),
            offer_row(energy_price_eur_per_mwh=50.0, adj_quantity_mw=10.0),
            offer_row(purpose_cd="BID", energy_price_eur_per_mwh=80.0, adj_quantity_mw=6.0),
            offer_row(purpose_cd="BID", energy_price_eur_per_mwh=120.0, adj_quantity_mw=2.0),
        ]
    )
    curves, _ = bid_curves_from_offers(offers, delivery_day=DAY, zones=("NORD",))
    supply = curves[(1, "NORD")]["supply"]
    demand = curves[(1, "NORD")]["demand"]
    # Supply rises with price, demand falls; volumes are cumulative.
    assert supply.prices == (50.0, 60.0)
    assert supply.cumulative_volumes == (10.0, 14.0)
    assert demand.prices == (120.0, 80.0)
    assert demand.cumulative_volumes == (2.0, 8.0)


def test_block_volumes_merge_into_the_curve_at_the_same_price():
    offers = pd.DataFrame(
        [
            offer_row(purpose_cd="BID", energy_price_eur_per_mwh=4000.0, adj_quantity_mw=5.0),
            offer_row(
                offer_type="B",
                purpose_cd="BID",
                status_cd="ACC",
                energy_price_eur_per_mwh=1.0,
                awarded_quantity_mw=3.0,
            ),
        ]
    )
    curves, block_rows = bid_curves_from_offers(offers, delivery_day=DAY, zones=("NORD",))
    demand = curves[(1, "NORD")]["demand"]
    assert block_rows == 1
    # The accepted block is price-taking at the cap, so it merges with the
    # existing 4000-EUR demand step instead of adding a duplicate price.
    assert demand.prices == (4000.0,)
    assert demand.cumulative_volumes == (8.0,)


def test_orders_from_zonal_curves_assigns_ids_and_drops_empty_rows():
    curves = pd.DataFrame(
        [
            {"delivery_day": DAY, "period": 1, "zone": "NORD", "side": "supply", "price_eur_per_mwh": 50.0, "quantity_mwh": 10.0},
            {"delivery_day": DAY, "period": 1, "zone": "NORD", "side": "demand", "price_eur_per_mwh": 100.0, "quantity_mwh": 0.0},
        ]
    )
    orders = orders_from_zonal_curves(curves)
    assert len(orders) == 1
    assert orders.iloc[0]["id"] == "NORD_supply_1_0"
