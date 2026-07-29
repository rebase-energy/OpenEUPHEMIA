"""Reproduce the published Italian day-ahead zonal prices for one day.

Builds the market step by step — zones and interconnectors, aggregated
bid curves, transfer capacities, boundary conditions — and clears it.
Everything is downloaded from GME's website on the first run and cached
under data/gme afterwards. No observed flows, no tie-break rules.

Run from the repository root:

    python examples/replicate_one_day.py
"""

from openeuphemia import Market, System
from openeuphemia.gme.prices import mgp_price_mapping
from openeuphemia.italy.curves import ITALY_PRICE_AREAS, bid_curves_from_offers
from openeuphemia.italy.data import (
    load_or_fetch_capacity_bounds,
    load_or_fetch_offers,
    load_or_fetch_prices,
)
from openeuphemia.italy.replication import (
    compare_prices,
    external_boundary_prices_from_bounds,
    external_capacity_bounds_from_all_bounds,
    internal_transfer_capacities,
    summarize_price_comparison,
)

DAY = "2025-04-01"

# ---------------------------------------------------------------------------
# 1. Public GME inputs: the full order book (offerte pubbliche), the
#    transfer-capacity limits (Limiti di transito), and the published zonal
#    prices (which close the borders and validate the result).
# ---------------------------------------------------------------------------
offers = load_or_fetch_offers(DAY)
capacity_bounds = load_or_fetch_capacity_bounds(DAY)
published_prices = mgp_price_mapping(load_or_fetch_prices(DAY), delivery_day=DAY)

# ---------------------------------------------------------------------------
# 2. Topology: the seven Italian bidding zones and the links between them.
# ---------------------------------------------------------------------------
capacities = internal_transfer_capacities(
    capacity_bounds,
    delivery_day=DAY,
    zones=ITALY_PRICE_AREAS,
)
system = System(
    zones=list(ITALY_PRICE_AREAS),
    interconnectors=sorted({(row.from_zone, row.to_zone) for row in capacities.itertuples()}),
)
market = Market(name="italy", delivery_day=DAY, system=system, periods=list(range(1, 25)))

# ---------------------------------------------------------------------------
# 3. Orders: one aggregated supply and demand curve per zone and hour, summed
#    from every published bid and offer. Accepted 2025 block orders enter as
#    price-taking volumes at their published awarded quantity.
# ---------------------------------------------------------------------------
bid_curves, block_fixing_rows = bid_curves_from_offers(
    offers,
    delivery_day=DAY,
    zones=ITALY_PRICE_AREAS,
)
for (period, zone), sides in sorted(bid_curves.items()):
    market.add_bid_curve(zone=zone, period=period, **sides)

# ---------------------------------------------------------------------------
# 4. Internal network: the published transfer capacity of each link, per hour.
# ---------------------------------------------------------------------------
for row in capacities.itertuples():
    market.set_ntc(
        row.from_zone,
        row.to_zone,
        period=row.period,
        forward_capacity_mwh=row.forward_capacity_mwh,
        reverse_capacity_mwh=row.reverse_capacity_mwh,
    )

# ---------------------------------------------------------------------------
# 5. Boundary conditions: every external border becomes a price taker at the
#    neighbouring zone's published price, free to trade within the published
#    border capacity. This is the only place the rest of Europe enters.
# ---------------------------------------------------------------------------
external_bounds = external_capacity_bounds_from_all_bounds(
    capacity_bounds,
    delivery_day=DAY,
    zones=ITALY_PRICE_AREAS,
)
border_prices, _diagnostics = external_boundary_prices_from_bounds(
    external_bounds,
    {key: value for key, value in published_prices.items() if key[1] not in ITALY_PRICE_AREAS},
)
for row in border_prices.itertuples():
    market.add_fixed_price_boundary(
        id=row.id,
        period=row.period,
        zone=row.zone,
        external_zone=row.external_zone,
        price_eur_per_mwh=row.price_eur_per_mwh,
        import_capacity_mwh=row.import_capacity_mwh,
        export_capacity_mwh=row.export_capacity_mwh,
    )

print(
    f"built {len(market.orders)} orders "
    f"({block_fixing_rows} accepted block orders), "
    f"{len(market.boundary_prices)} price-taking borders"
)

# ---------------------------------------------------------------------------
# 6. Clear. Zonal prices are the duals of the zonal balance constraints.
# ---------------------------------------------------------------------------
result = market.clear(method="per-period-lp")

# ---------------------------------------------------------------------------
# 7. Compare with the published prices, hour by hour and zone by zone.
# ---------------------------------------------------------------------------
comparison = compare_prices(
    result.prices,
    {key: value for key, value in published_prices.items() if key[1] in ITALY_PRICE_AREAS},
    delivery_day=DAY,
)
summary = summarize_price_comparison(comparison)
print(f"exact prices        {summary['exact_rows']}/{summary['matched_rows']}")
print(f"mean absolute error {summary['price_mae_eur_per_mwh']:.4f} EUR/MWh")
print(f"max absolute error  {summary['price_max_abs_error_eur_per_mwh']:.4f} EUR/MWh")

print("\nfirst hours of the comparison:")
columns = ["period", "zone", "modelled_price_eur_per_mwh", "published_price_eur_per_mwh"]
print(comparison[columns].head(14).to_string(index=False))
