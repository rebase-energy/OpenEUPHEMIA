# Italy validation inputs

Tidy market inputs for the Italian day-ahead (MGP) price replication, one
directory per month. Everything here is derived from public publications
by [GME](https://www.mercatoelettrico.org) — free to download and use, no
authentication required.

| File | Columns | Source | Role |
|---|---|---|---|
| `bid-curves.csv.gz` | `delivery_day, period, zone, side, price_eur_per_mwh, quantity_mwh` | MGP *Offerte pubbliche* | input |
| `simple-bid-curves.csv.gz` | same as `bid-curves` | MGP *Offerte pubbliche* | input, blocks **excluded** (see below) |
| `block-orders.csv.gz` | `delivery_day, block_id, period, zone, side, quantity_mwh, price_eur_per_mwh, published_status` | MGP *Offerte pubbliche* | input; `published_status` is **target only** |
| `transfer-capacities.csv.gz` | `delivery_day, period, id, from_zone, to_zone, min_flow_mwh, max_flow_mwh` | MGP *Limiti di transito* | input |
| `internal-transfer-capacities.csv.gz` | `delivery_day, period, from_zone, to_zone, forward_capacity_mwh, reverse_capacity_mwh` | derived (see below) | convenience view of `transfer-capacities`, ready for `set_ntc` |
| `published-prices.csv.gz` | `delivery_day, period, zone, price_eur_per_mwh` | MGP *Prezzi* | border zones are input, Italian zones are the target |
| `published-exchanges.csv.gz` | `delivery_day, period, zone, external_zone, exchange_mwh` | MGP *Transiti* | input for the `exchanges` boundary |
| `published-flows.csv.gz` | `delivery_day, period, from_zone, to_zone, flow_mwh` | MGP *Transiti* | **target only** |

`published-flows` covers the six links *between* Italian zones and is never
an input: it is what the flow validation is scored against.
`published-exchanges` covers the twelve *cross-border* links and is an
input under the `exchanges` boundary — the schedule with the rest of
Europe, which Italy's clearing takes as given. Both are export-positive
from the first-named zone.

`internal-transfer-capacities` carries no information beyond
`transfer-capacities` — it is the same six internal links, filtered to
drop the twelve cross-border edges and re-expressed as one non-negative
`forward_capacity_mwh`/`reverse_capacity_mwh` pair per link instead of a
signed `min_flow_mwh`/`max_flow_mwh` range. That is exactly the shape
`PowerMarket.set_ntc` takes, so it is committed as its own file rather
than requiring every consumer to re-derive it with
`openeuphemia.areas.italy.replication.internal_transfer_capacities`
(which is what produced this file, and which `build_italy_market` still
calls internally).

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
That is what `bid-curves.csv.gz` contains.

**Blocks left to the solver.** `simple-bid-curves.csv.gz` and
`block-orders.csv.gz` are the same order book split the other way, for
runs that decide block acceptance rather than replaying it: the curves
hold the simple hourly offers only, and each block is kept intact as its
own set of legs sharing a `block_id`, with one limit price and one
per-hour volume. `published_status` (`ACC` / `REJ` / `PREJ`) records what
GME decided and is used **only for scoring** — never fed to the solver.
A block is identified by its submitting unit and transaction reference;
withdrawn/superseded submissions (`REP`) are excluded, as they carry no
decision. See [`../../docs/block-orders.md`](../../docs/block-orders.md).

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

## Regenerating a derived file

`internal-transfer-capacities.csv.gz` is a pure transform of
`transfer-capacities.csv.gz` already committed in this directory — no
network access needed to rebuild it:

```python
import pandas as pd
from openeuphemia.areas.italy import ITALY_PRICE_AREAS, delivery_days
from openeuphemia.areas.italy.replication import internal_transfer_capacities

transfer_capacities = pd.read_csv("transfer-capacities.csv.gz")
frames = []
for day in delivery_days(transfer_capacities):
    frame = internal_transfer_capacities(
        transfer_capacities, delivery_day=day, zones=ITALY_PRICE_AREAS
    )
    frame.insert(0, "delivery_day", day)
    frames.append(frame)
pd.concat(frames, ignore_index=True).to_csv(
    "internal-transfer-capacities.csv.gz", index=False, compression="gzip"
)
```
