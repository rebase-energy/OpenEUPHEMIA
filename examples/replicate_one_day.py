"""Reproduce the published Italian day-ahead zonal prices for one day.

Downloads everything it needs from GME's website on the first run
(cached under data/gme afterwards) and clears the Italian market from
the public order book alone — no observed flows, no tie-break rules.

Run from the repository root:

    python examples/replicate_one_day.py
"""

from openeuphemia.italy.data import (
    load_or_fetch_capacity_bounds,
    load_or_fetch_offers,
    load_or_fetch_prices,
)
from openeuphemia.italy.replication import replicate_italy_day

DAY = "2025-04-01"

# 1. Public GME inputs: the full order book (offerte pubbliche), the
#    transfer-capacity limits (Limiti di transito), and the published
#    zonal prices (used for the border boundary condition and validation).
offers = load_or_fetch_offers(DAY)
capacity_bounds = load_or_fetch_capacity_bounds(DAY)
prices = load_or_fetch_prices(DAY)

# 2. Build and clear the market: aggregated zonal curves, internal
#    transfer capacities, price-taking boundaries at the published
#    border prices. Zonal prices come from the LP duals.
result = replicate_italy_day(
    delivery_day=DAY,
    offers=offers,
    capacity_bounds=capacity_bounds,
    reference_prices=prices,
)

# 3. Compare with the published prices, hour by hour and zone by zone.
summary = result.summary
print(f"delivery day        {summary['delivery_day']}")
print(f"orders cleared      {summary['orders']}")
print(f"exact prices        {summary['exact_rows']}/{summary['matched_rows']}")
print(f"mean absolute error {summary['price_mae_eur_per_mwh']:.4f} EUR/MWh")
print(f"max absolute error  {summary['price_max_abs_error_eur_per_mwh']:.4f} EUR/MWh")

print("\nfirst hours of the comparison:")
columns = ["period", "zone", "modelled_price_eur_per_mwh", "published_price_eur_per_mwh"]
print(result.price_comparison[columns].head(14).to_string(index=False))
