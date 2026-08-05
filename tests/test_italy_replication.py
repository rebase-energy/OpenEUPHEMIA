"""End-to-end price replication on a small synthetic Italy-like market."""

import pandas as pd
import pytest

from openeuphemia.italy.replication import (
    build_italy_market,
    external_boundary_prices,
    external_capacity_bounds,
    internal_transfer_capacities,
    replicate_italy_day,
)

DAY = "2025-04-01"


def curve_row(zone, side, price, quantity, period=1):
    return {
        "delivery_day": DAY,
        "period": period,
        "zone": zone,
        "side": side,
        "price_eur_per_mwh": price,
        "quantity_mwh": quantity,
    }


def synthetic_inputs():
    bid_curves = pd.DataFrame(
        [
            curve_row("NORD", "supply", 10.0, 100.0),
            curve_row("NORD", "demand", 4000.0, 40.0),
            curve_row("SUD", "supply", 50.0, 100.0),
            curve_row("SUD", "demand", 4000.0, 60.0),
        ]
    )
    transfer_capacities = pd.DataFrame(
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
    published_prices = pd.DataFrame(
        [
            {"delivery_day": DAY, "period": 1, "zone": "NORD", "price_eur_per_mwh": 30.0},
            {"delivery_day": DAY, "period": 1, "zone": "SUD", "price_eur_per_mwh": 50.0},
            {"delivery_day": DAY, "period": 1, "zone": "XFRA", "price_eur_per_mwh": 30.0},
        ]
    )
    return bid_curves, transfer_capacities, published_prices


def test_replicates_published_prices_exactly():
    bid_curves, capacities, prices = synthetic_inputs()
    result = replicate_italy_day(
        delivery_day=DAY,
        bid_curves=bid_curves,
        transfer_capacities=capacities,
        published_prices=prices,
        zones=("NORD", "SUD"),
    )
    assert result.summary["exact_rows"] == 2
    assert result.summary["matched_rows"] == 2
    assert result.summary["price_mae_eur_per_mwh"] == 0.0
    assert result.summary["dropped_unpriced_borders"] == 0
    assert (result.boundary_diagnostics["treatment"] == "open").all()


def test_only_the_requested_day_is_used():
    bid_curves, capacities, prices = synthetic_inputs()
    other = bid_curves.copy()
    other["delivery_day"] = "2025-04-02"
    other["quantity_mwh"] = 999.0
    combined = pd.concat([bid_curves, other], ignore_index=True)
    result = replicate_italy_day(
        delivery_day=DAY,
        bid_curves=combined,
        transfer_capacities=capacities,
        published_prices=prices,
        zones=("NORD", "SUD"),
    )
    assert result.summary["exact_rows"] == 2


def test_market_declares_zones_and_interconnectors_with_bid_curves_and_boundaries():
    bid_curves, capacities, prices = synthetic_inputs()
    built = build_italy_market(
        delivery_day=DAY,
        bid_curves=bid_curves,
        transfer_capacities=capacities,
        published_prices=prices,
        zones=("NORD", "SUD"),
    )
    market = built.market
    assert market.has_interconnector("NORD", "SUD")
    # The one external border became a price-taking boundary, not a link.
    assert list(market.boundary_prices["external_zone"]) == ["XFRA"]
    assert market.boundary_flows.empty
    market.validate()
    link = market.interconnectors.iloc[0]
    assert (link["min_flow_mwh"], link["max_flow_mwh"]) == (-20.0, 20.0)


def test_missing_day_is_rejected():
    bid_curves, capacities, prices = synthetic_inputs()
    with pytest.raises(ValueError, match="no bid curve rows"):
        build_italy_market(
            delivery_day="2025-04-02",
            bid_curves=bid_curves,
            transfer_capacities=capacities,
            published_prices=prices,
            zones=("NORD", "SUD"),
        )


def test_internal_transfer_capacities_are_non_negative_and_directional():
    _, capacities, _ = synthetic_inputs()
    asymmetric = capacities.copy()
    asymmetric.loc[0, ["min_flow_mwh", "max_flow_mwh"]] = [-5.0, 20.0]
    result = internal_transfer_capacities(
        asymmetric,
        delivery_day=DAY,
        zones=("NORD", "SUD"),
    )
    row = result.iloc[0]
    assert row["from_zone"] == "NORD"
    assert row["to_zone"] == "SUD"
    assert row["forward_capacity_mwh"] == 20.0
    assert row["reverse_capacity_mwh"] == 5.0


def test_external_bounds_are_oriented_export_positive():
    _, capacities, _ = synthetic_inputs()
    flipped = capacities.copy()
    # Same border stated from the external side: XFRA -> NORD with
    # asymmetric limits (200 towards NORD, 500 towards XFRA).
    flipped.loc[1, ["id", "from_zone", "to_zone", "min_flow_mwh", "max_flow_mwh"]] = [
        "XFRA_NORD",
        "XFRA",
        "NORD",
        -500.0,
        200.0,
    ]
    external = external_capacity_bounds(
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
    boundary_prices, diagnostics = external_boundary_prices(external, {})
    assert boundary_prices.empty
    assert list(diagnostics["treatment"]) == ["unpriced-dropped"]
