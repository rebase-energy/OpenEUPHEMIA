# OpenEUPHEMIA

**An open-source replication of EUPHEMIA, the European day-ahead electricity market-clearing algorithm — built only from public data.**

[EUPHEMIA](https://www.nemo-committee.eu/assets/files/euphemia-public-description.pdf) clears the Single Day-ahead Coupling (SDAC) spanning most of Europe: it maximizes economic welfare over all submitted orders subject to network constraints, and the resulting zonal prices settle billions of euros of energy every year. The algorithm is publicly described but its implementation is closed, and the full order books it clears are not public.

OpenEUPHEMIA's aim is a complete, verifiable open-source implementation for the full SDAC region. The approach is incremental and evidence-driven: each market region is added only once its published outcomes can be **reproduced exactly** from public data, so that everything in this repository is proven to work.

## Current milestone: Italy, exact price replication

The first milestone replicates the published Italian day-ahead (MGP) zonal prices for **April 2025 — 5,040 of 5,040 zone-hours exact (MAE 0.0000 EUR/MWh, maximum absolute error 0.000 EUR/MWh)** — using nothing but public data published by [GME](https://www.mercatoelettrico.org):

| Input | GME publication |
|---|---|
| Full order book (every bid/offer with price, quantity, zone, acceptance status) | *Offerte pubbliche* (published with ~1 week delay) |
| Transfer capacities between bidding zones and across external borders | *Limiti di transito* |
| Published zonal prices (used for the boundary condition and for validation) | *MGP Prezzi* |

### How it works

Italy is cleared as a welfare-maximizing linear program per delivery period:

1. **Aggregated zonal curves.** All simple hourly offers with a published ACC/REJ status are summed into one supply and one demand curve per (zone, period). Block orders (all-or-nothing across periods, introduced to the MGP in 2025) cannot live inside a convex curve; following EUPHEMIA's own price decomposition, their published decisions are fixed — accepted blocks enter as price-taking volumes, rejected blocks are dropped.
2. **Internal network.** The seven Italian bidding zones (NORD, CNOR, CSUD, SUD, CALA, SICI, SARD) are connected by transfer capacities from the published *Limiti di transito*.
3. **Price-taking boundaries.** Every external border (France, Switzerland, Austria, Slovenia, Greece, Montenegro, Corsica, …) is modelled as a price-taker at the neighbouring zone's published price, free to exchange anywhere within the published border capacities — a Dirichlet boundary condition. **No observed flows, scheduled exchanges, or tie-break rules are used anywhere.**
4. **Prices from duals.** Zonal prices are the dual values of the zonal balance constraints, solved with [HiGHS](https://highs.dev).

That this closure reproduces every published price exactly is the point: given the published order book and capacities, the welfare-maximization problem has a unique price solution that matches GME's, and the border prices alone are a sufficient statistic for the rest of Europe.

## Installation

Requires Python ≥ 3.11.

```bash
git clone https://github.com/rebase-energy/OpenEUPHEMIA.git
cd OpenEUPHEMIA
pip install -e .
```

or with [uv](https://docs.astral.sh/uv/):

```bash
uv sync
```

## Usage

Replicate a day (or a range) — all inputs are downloaded from GME on first use and cached under `data/gme`:

```bash
python scripts/replicate_italy_prices.py 2025-04-01
python scripts/replicate_italy_prices.py 2025-04-01 2025-04-30
```

Expected output, for every single day of April 2025:

```
2025-04-01  MAE 0.0000  max 0.000  exact 168/168  dropped-borders 0
...
TOTAL  MAE 0.0000  max 0.000  exact 5040/5040
```

Or from Python, in one call:

```python
from openeuphemia.italy.data import (
    load_or_fetch_offers,
    load_or_fetch_capacity_bounds,
    load_or_fetch_prices,
)
from openeuphemia.italy.replication import replicate_italy_day

day = "2025-04-01"
result = replicate_italy_day(
    delivery_day=day,
    offers=load_or_fetch_offers(day),
    capacity_bounds=load_or_fetch_capacity_bounds(day),
    reference_prices=load_or_fetch_prices(day),
)
print(result.summary)
result.price_comparison  # one row per (period, zone) with modelled vs published price
```

### Building a market

A market is assembled incrementally — zones and interconnectors, aggregated bid curves, transfer capacities, boundary conditions — and then cleared:

```python
from openeuphemia import BidCurve, Market, System

system = System(zones=["NORD", "SUD"], interconnectors=[("NORD", "SUD")])
market = Market(name="example", delivery_day="2025-04-01", system=system, periods=[1])

market.add_bid_curve(
    zone="NORD",
    period=1,
    supply=BidCurve(prices=[10.0, 80.0], cumulative_volumes=[100.0, 200.0]),
    demand=BidCurve(prices=[4000.0, 30.0], cumulative_volumes=[40.0, 120.0]),
)
market.set_ntc("NORD", "SUD", capacity_mwh=500.0)
market.add_fixed_price_boundary(       # a price-taking neighbour
    id="NORD_FRAN", period=1, zone="NORD", external_zone="FRAN",
    price_eur_per_mwh=60.0, import_capacity_mwh=1000.0, export_capacity_mwh=1000.0,
)

result = market.clear(method="per-period-lp")
result.prices
```

Boundaries come in both flavours: `add_fixed_price_boundary` for a price-taking neighbour (used here for Italy) and `add_fixed_flow_boundary` to pin an exchange at a known volume.

[`examples/replicate_one_day.py`](examples/replicate_one_day.py) applies exactly these steps to real GME data and reproduces one full day of published prices.

> **Note.** GME publishes the order book with roughly one week of delay, so the most recent days cannot be replicated until their *offerte pubbliche* appear.

## Roadmap

- **Italy** — exact zonal price replication (this milestone). Next: cleared volumes and scheduled flows.
- **Nordics / MIBEL / CWE** — extend the same aggregated-curve + boundary-condition methodology, region by region, each gated on exact replication of published outcomes.
- **Full SDAC** — one coupled clearing of all regions, closing the boundary conditions internally.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

## License

[MIT](LICENSE)

## About

Built by [rebase.energy](https://rebase.energy). Contributions and replication reports are welcome — please open an issue.
