# Italy validation inputs

Tidy market inputs for the Italian day-ahead (MGP) price replication, one
directory per month. Everything here is derived from public publications
by [GME](https://www.mercatoelettrico.org) — free to download and use, no
authentication required.

| File | Columns | Source | Role |
|---|---|---|---|
| `bid-curves.csv.gz` | `delivery_day, period, zone, side, price_eur_per_mwh, quantity_mwh` | MGP *Offerte pubbliche* | input |
| `transfer-capacities.csv.gz` | `delivery_day, period, id, from_zone, to_zone, min_flow_mwh, max_flow_mwh` | MGP *Limiti di transito* | input |
| `published-prices.csv.gz` | `delivery_day, period, zone, price_eur_per_mwh` | MGP *Prezzi* | border zones are input, Italian zones are the target |
| `published-exchanges.csv.gz` | `delivery_day, period, zone, external_zone, exchange_mwh` | MGP *Transiti* | input for the `exchanges` boundary |
| `published-flows.csv.gz` | `delivery_day, period, from_zone, to_zone, flow_mwh` | MGP *Transiti* | **target only** |

`published-flows` covers the six links *between* Italian zones and is never
an input: it is what the flow validation is scored against.
`published-exchanges` covers the twelve *cross-border* links and is an
input under the `exchanges` boundary — the schedule with the rest of
Europe, which Italy's clearing takes as given. Both are export-positive
from the first-named zone.

## How the inputs were derived

Data collection is deliberately **not** part of the library — OpenEUPHEMIA
is about building, closing, and clearing markets. The inputs were prepared
once and committed so that the notebook and the validation script run
offline and reproducibly.

**Bid curves.** GME's *Offerte pubbliche* publishes the complete order
book: every bid and offer with its unit, zone, price, quantity, and
acceptance status. Simple hourly offers carrying a published `ACC` or
`REJ` status are summed per `(period, zone, side, price)` into the
aggregated curves here. Rejected offers are kept — they define the shape
of the curve above (or below) the clearing price.

**Block orders.** From 2025 the MGP accepts block orders, which are
all-or-nothing across several periods and therefore cannot be represented
inside a convex curve: an accepted block may sit above the zonal price
and a paradoxically rejected block below it. Following EUPHEMIA's own
price decomposition, their published decisions are fixed — accepted
blocks enter the curves as price-taking volumes at their awarded
quantity (supply below the floor, demand at the cap), rejected blocks are
dropped — and prices come from clearing the remaining convex market.

**Transfer capacities.** *Limiti di transito* publishes a directional
limit per edge and period. Paired directions become one row with
`max_flow_mwh` for `from_zone → to_zone` and `min_flow_mwh` (negative)
for the reverse. Both internal edges and external borders are included;
the library decides which is which.

**Published prices.** *Prezzi* covers the seven Italian zones and the
neighbouring border zones. The border-zone prices close the model as
price-taking boundaries; the Italian ones are the validation target and
are never given to the solver.

**Transits.** *Transiti* publishes the realized schedule of every edge per
period. Split by endpoint, the internal edges become the flow validation
target and the cross-border edges the exchange input.

## Coverage

| Month | Days | Zone-hours | Link-hours | Notes |
|---|---|---|---|---|
| `2025-04` | 30 | 5,040 | 4,320 | Hourly periods, 7 zones, 6 internal links, 12 external borders |
