# OpenEUPHEMIA

**This project aims to be an open-source replication of EUPHEMIA, the European day-ahead electricity market-clearing algorithm — validated against published market results.**

[EUPHEMIA](https://www.nemo-committee.eu/sdac) clears the Single Day-ahead Coupling (SDAC) spanning most of Europe: it maximizes economic welfare over all submitted orders subject to network constraints, and the resulting zonal prices settle billions of euros of energy every year. The algorithm is publicly described but its implementation is closed.

OpenEUPHEMIA's aim is a complete, verifiable open-source implementation for the full SDAC region. The approach is incremental and validation-driven: a market region is added only once its published outcomes can be **reproduced exactly**, so everything in this repository is proven to work.

## Validation cases

A market clearing publishes two outcomes: the **zonal prices** and the **flows** between zones. A case is validated against both.

| Case | Period | Prices | Flows | Run it |
|---|---|---|---|---|
| **Italy** (GME MGP) | April 2025 | ✅ **5,040 / 5,040 zone-hours exact** — MAE 0.0000 EUR/MWh | 🚧 not yet validated | [notebook](examples/italy_april_2025.ipynb) · [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/rebase-energy/OpenEUPHEMIA/blob/main/examples/italy_april_2025.ipynb) |

Each case builds its market from published data only, clears it, and compares against the outcome the market operator published. Crucially, **no observed flows or tie-break rules are used**: scheduled exchanges are an *outcome* of the clearing, so feeding them back in would make the exercise circular.

Prices come first because they are pinned down uniquely by welfare maximization. Flows are not: whenever two zones settle at the same price, the split of the exchange between them is genuinely indeterminate — many flow patterns support the identical, optimal welfare — so reproducing the published flows needs a further rule that is not in the public description. That is the next milestone, and until a case reproduces flows exactly its column stays marked 🚧.

## How a case works

A market is assembled from three tidy tables and cleared as a welfare-maximizing linear program per delivery period:

1. **Bid curves** — every submitted bid and offer, aggregated into one supply and one demand curve per zone and period.
2. **Transfer capacities** — the published limit of each link between zones.
3. **Boundary conditions** — each border with the un-modelled world becomes a *price taker* at the neighbouring zone's published price, bounded by the published border capacity (a Dirichlet boundary condition). The border prices turn out to be a sufficient statistic for the rest of Europe.

Zonal prices are then the dual values of the zonal balance constraints, solved with [HiGHS](https://highs.dev) — exactly how EUPHEMIA defines them.

## Installation

Requires Python ≥ 3.11.

```bash
git clone https://github.com/rebase-energy/OpenEUPHEMIA.git
cd OpenEUPHEMIA
pip install -e .
```

or with [uv](https://docs.astral.sh/uv/): `uv sync`.

## Building a market

Markets are built incrementally — zones and interconnectors, bid curves, transfer capacities, boundary conditions — then cleared:

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
market.add_fixed_price_boundary(          # a price-taking neighbour
    id="NORD_FRAN", period=1, zone="NORD", external_zone="FRAN",
    price_eur_per_mwh=60.0, import_capacity_mwh=1000.0, export_capacity_mwh=1000.0,
)

result = market.clear(method="per-period-lp")
result.prices
```

Boundaries come in both flavours: `add_fixed_price_boundary` for a price-taking neighbour and `add_fixed_flow_boundary` to pin an exchange at a known volume. `BidCurve.from_steps` builds a curve from unsorted (price, quantity) pairs, and `bid_curves_from_table` builds a whole market's worth from a dataframe.

The [Italy notebook](examples/italy_april_2025.ipynb) applies exactly these steps to real data and reproduces a full month of published prices.

## Reproducing the Italy case

The validation inputs are committed under [`data/italy`](data/italy), so everything runs offline:

```bash
python scripts/replicate_italy_prices.py            # all 30 days of April 2025
python scripts/replicate_italy_prices.py --day 2025-04-01
```

```
2025-04-01  MAE 0.0000  max 0.000  exact 168/168  dropped-borders 0
...
TOTAL  MAE 0.0000  max 0.000  exact 5040/5040
```

## Scope

This library is about **building, closing, and clearing markets** — not about collecting data. Each case's inputs are prepared once from the market operator's public publications and committed as tidy tables; [`data/italy/README.md`](data/italy/README.md) documents exactly what they are and how they were derived (including how non-convex block orders are handled).

## Roadmap

- **Italy** — prices replicated exactly ✅. Next: the flows, and with them the cleared volumes per zone.
- **Nordics / MIBEL / CWE** — extend the same curve + boundary-condition methodology, region by region, each gated on exact replication of published outcomes.
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
