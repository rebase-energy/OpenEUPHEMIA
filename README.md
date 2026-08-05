# OpenEUPHEMIA

**This project aims to be an open-source replication of EUPHEMIA, the European day-ahead electricity market-clearing algorithm — validated against published market results.**

[EUPHEMIA](https://www.nemo-committee.eu/sdac) clears the Single Day-ahead Coupling (SDAC) spanning most of Europe: it maximizes economic welfare over all submitted orders subject to network constraints, and the resulting zonal prices settle billions of euros of energy every year. The algorithm is publicly described but its implementation is closed.

OpenEUPHEMIA's aim is a complete, verifiable open-source implementation for the full SDAC region. The approach is incremental and validation-driven: a market region is added only once its published outcomes can be **reproduced exactly**, so everything in this repository is proven to work.

## Validation cases

A market clearing publishes two outcomes: the **zonal prices** and the **flows** between zones. A case is validated against both.

| Case | Period | Prices | Flows | Run it |
|---|---|---|---|---|
| **Italy** (GME MGP) | April 2025 | ✅ **5,040 / 5,040 zone-hours exact** — MAE 0.0000 EUR/MWh | ✅ **0.0012 MWh MAE** over 4,320 link-hours — max 0.10 MWh | [notebook](examples/italy_april_2025.ipynb) · [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/rebase-energy/OpenEUPHEMIA/blob/main/examples/italy_april_2025.ipynb) |

Each case builds its market from published data only, clears it, and compares against the outcome the market operator published. Neither result uses the quantity it predicts: the Italian zonal prices and the flows between Italian zones are held back for scoring.

The two halves are different in kind.

**Prices follow from welfare maximization alone.** Given the published order book and capacities, the optimum has a unique price solution and it is the one GME published — no rule, no tuning. It needs only the neighbours' published prices to close the borders; France and Greece are never modelled, merely priced.

**Flows need a selection rule on top.** Whenever two zones settle at the same price the exchange between them is genuinely indeterminate: many flow patterns support the identical, maximal welfare, so the solver returns an arbitrary one. A *selection rule* — a secondary objective over the welfare optimum, which by construction leaves the prices untouched — decides which. Measured over April 2025:

| Flow selection | Flow MAE | Exact link-hours | Reads the answer? |
|---|---|---|---|
| none — arbitrary optimal vertex | 2.89 MWh | 3,818 / 4,320 | no |
| volume maximization (documented) | 2.90 MWh | 3,814 / 4,320 | no |
| **pro-rata sharing** (learned) | **0.0012 MWh** | 4,208 / 4,320 | no |
| anchored to the published schedule | 0.0001 MWh | 4,311 / 4,320 | yes — a reference bound, not a prediction |

Pro-rata — sharing tied same-price acceptance in proportion to submitted volume — removes three orders of magnitude of error while consuming no outcome data, and lands essentially on the anchored floor. Two caveats stated plainly: it is **not in EUPHEMIA's public description** but reverse-engineered from published outcomes, and it is **era-dependent** — before the 2025 abolition of the PUN the published splits instead follow the description's documented merit-order priority rule, which needs an order book rather than aggregated curves.

Scoring flows also requires closing the borders differently. A price-taking border is free to trade any volume within its capacity, so the border volume floats and drags the internal flows with it; the flow figures above pin each border at its published exchange instead (`boundary="exchanges"`). That schedule with the rest of Europe is an *input* to Italy's problem — it says nothing about how flow splits between Italian zones, which is what is being predicted.

## How a case works

A market is assembled from three tidy tables and cleared as a welfare-maximizing linear program per delivery period:

1. **Bid curves** — every submitted bid and offer, aggregated into one supply and one demand curve per zone and period.
2. **Transfer capacities** — the published limit of each link between zones.
3. **Boundary conditions** — each border with the un-modelled world is closed either as a *price taker* at the neighbouring zone's published price (Dirichlet) or at a known exchange volume (Neumann).

Zonal prices are then the dual values of the zonal balance constraints, solved with [HiGHS](https://highs.dev) — exactly how EUPHEMIA defines them. An optional **flow selection** rule resolves what welfare leaves undecided about the flows.

## Installation

Requires Python ≥ 3.11.

```bash
git clone https://github.com/rebase-energy/OpenEUPHEMIA.git
cd OpenEUPHEMIA
pip install -e .
```

or with [uv](https://docs.astral.sh/uv/): `uv sync`.

## Building a market

A `PowerMarket` declares its own zones and interconnectors — no separate topology object — and is built up incrementally: bid curves, transfer capacities, boundary conditions, then cleared:

```python
from openeuphemia import BidCurve, PowerMarket

market = PowerMarket(
    name="example",
    delivery_day="2025-04-01",
    zones=["NORD", "SUD"],
    interconnectors=[("NORD", "SUD")],
    periods=[1],
)

market.add_bid_curve(
    zone="NORD",
    period=1,
    supply=BidCurve([(100.0, 10.0), (200.0, 80.0)]),      # [(volume, price), ...]
    demand=BidCurve([(40.0, 4000.0), (120.0, 30.0)]),
)
market.set_ntc("NORD", "SUD", capacity_mwh=500.0)
market.add_fixed_price_boundary(                # a price-taking neighbour
    id="NORD_FRAN", period=1, zone="NORD", external_zone="FRAN",
    price_eur_per_mwh=60.0, import_capacity_mwh=1000.0, export_capacity_mwh=1000.0,
)

result = market.clear(method="per-period-lp")
result.prices
```

`BidCurve`'s default constructor takes a list of `(volume, price)` pairs; passing `prices`/`cumulative_volumes` as two separate sequences also works, for callers that already have the curve in that shape. `BidCurve.from_steps` builds one from unsorted per-step `(price, quantity)` data, and `bid_curves_from_table` builds a whole market's worth from a dataframe.

Boundary conditions are two separate methods, since they take different arguments: `add_fixed_price_boundary` for a price-taking neighbour (Dirichlet — free to trade within a capacity, at a fixed price) and `add_fixed_flow_boundary` to pin an exchange at a known volume (Neumann).

To resolve which of the welfare-equal flow patterns is returned, pass a selection rule — the prices are read before it applies and stay untouched:

```python
market.clear(flow_selection="pro-rata")     # or "volume-max", or "anchored"
```

The [Italy notebook](examples/italy_april_2025.ipynb) applies exactly these steps to real data and reproduces a full month of published prices.

## Reproducing the Italy case

The validation inputs are committed under [`data/italy`](data/italy), so everything runs offline:

```bash
python scripts/replicate_italy_prices.py            # prices, all 30 days of April 2025
python scripts/replicate_italy_prices.py --boundary exchanges --flow-selection pro-rata
```

```
TOTAL  MAE 0.0000  max 0.000  exact 5040/5040
TOTAL  flow MAE 0.0012  max 0.100  exact 4208/4320
```

## Scope

This library is about **building, closing, and clearing markets** — not about collecting data. Each case's inputs are prepared once from the market operator's public publications and committed as tidy tables; [`data/italy/README.md`](data/italy/README.md) documents exactly what they are and how they were derived (including how non-convex block orders are handled).

## Roadmap

- **Italy** — prices and flows both replicated ✅. Open: closing the one remaining flow gap without pinning the border exchanges, and the floor-price curtailment hours, where the sharing set appears to be set by non-public configuration rather than by the optimization.
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
