"""End-to-end price replication on a small synthetic Italy-like market."""

import pandas as pd

from openeuphemia.italy.replication import (
    external_boundary_prices_from_bounds,
    external_capacity_bounds_from_all_bounds,
    replicate_italy_day,
)

DAY = "2025-04-01"


def offer_row(zone, purpose, price, quantity, period=1):
    return {
        "delivery_date": DAY,
        "interval_no": period,
        "zone_cd": zone,
        "purpose_cd": purpose,
        "status_cd": "ACC",
        "energy_price_eur_per_mwh": price,
        "adj_quantity_mw": quantity,
        "awarded_quantity_mw": quantity,
        "offer_type": "S",
    }


def synthetic_inputs():
    offers = pd.DataFrame(
        [
            offer_row("NORD", "OFF", 10.0, 100.0),
            offer_row("NORD", "BID", 4000.0, 40.0),
            offer_row("SUD", "OFF", 50.0, 100.0),
            offer_row("SUD", "BID", 4000.0, 60.0),
        ]
    )
    capacity_bounds = pd.DataFrame(
        [
            {
                "delivery_day": DAY,
                "period": 1,
                "id": "NORD_SUD",
                "from_zone": "NORD",
                "to_zone": "SUD",
                "min_flow_mwh": -20.0,
                "max_flow_mwh": 20.0,
            },
            {
                "delivery_day": DAY,
                "period": 1,
                "id": "NORD_XFRA",
                "from_zone": "NORD",
                "to_zone": "XFRA",
                "min_flow_mwh": -500.0,
                "max_flow_mwh": 500.0,
            },
        ]
    )
    # NORD exports its cheap surplus into the border zone until the border
    # price (30) is marginal in NORD; congestion keeps SUD at its own
    # 50-EUR supply.
    reference_prices = pd.DataFrame(
        [
            {"delivery_day": DAY, "market": "MGP", "period": 1, "zone": "NORD", "price_eur_per_mwh": 30.0, "source": "test"},
            {"delivery_day": DAY, "market": "MGP", "period": 1, "zone": "SUD", "price_eur_per_mwh": 50.0, "source": "test"},
            {"delivery_day": DAY, "market": "MGP", "period": 1, "zone": "XFRA", "price_eur_per_mwh": 30.0, "source": "test"},
        ]
    )
    return offers, capacity_bounds, reference_prices


def test_replicates_published_prices_exactly():
    offers, bounds, prices = synthetic_inputs()
    result = replicate_italy_day(
        delivery_day=DAY,
        offers=offers,
        capacity_bounds=bounds,
        reference_prices=prices,
        zones=("NORD", "SUD"),
    )
    assert result.summary["exact_rows"] == 2
    assert result.summary["matched_rows"] == 2
    assert result.summary["price_mae_eur_per_mwh"] == 0.0
    assert result.summary["dropped_unpriced_borders"] == 0
    assert (result.boundary_diagnostics["treatment"] == "open").all()


def test_external_bounds_are_oriented_export_positive():
    _, bounds, _ = synthetic_inputs()
    flipped = bounds.copy()
    # Same border stated from the external side: XFRA -> NORD with
    # asymmetric limits (200 towards NORD, 500 towards XFRA).
    flipped.loc[1, ["id", "from_zone", "to_zone", "min_flow_mwh", "max_flow_mwh"]] = [
        "XFRA_NORD",
        "XFRA",
        "NORD",
        -500.0,
        200.0,
    ]
    external = external_capacity_bounds_from_all_bounds(
        flipped,
        delivery_day=DAY,
        zones=("NORD", "SUD"),
    )
    assert len(external) == 1
    row = external.iloc[0]
    assert row["zone"] == "NORD"
    assert row["external_zone"] == "XFRA"
    assert row["import_capacity_mwh"] == 200.0
    assert row["export_capacity_mwh"] == 500.0


def test_unpriced_borders_are_dropped_with_diagnostics():
    external = pd.DataFrame(
        [
            {
                "id": "NORD_XFRA",
                "period": 1,
                "zone": "NORD",
                "external_zone": "XFRA",
                "import_capacity_mwh": 100.0,
                "export_capacity_mwh": 100.0,
            }
        ]
    )
    boundary_prices, diagnostics = external_boundary_prices_from_bounds(external, {})
    assert boundary_prices.empty
    assert list(diagnostics["treatment"]) == ["unpriced-dropped"]
