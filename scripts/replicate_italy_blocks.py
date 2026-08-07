"""Clear Italy with block orders decided by the solver, not replayed.

The main pipeline (``replicate_italy_prices.py``) takes GME's published
block accept/reject decisions as given, folding accepted blocks into the
bid curves as price-taking volume. This script does not: it hands every
block order to the MILP as an all-or-nothing binary and lets the solver
choose, then scores both the resulting prices and the block decisions
against what GME published.

See ``docs/block-orders.md`` for what this shows and where it stops.

Examples::

    python scripts/replicate_italy_blocks.py                  # whole month
    python scripts/replicate_italy_blocks.py --day 2025-04-01
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openeuphemia import PowerMarket, bid_curves_from_table
from openeuphemia.areas.italy import (
    ITALY_PRICE_AREAS,
    compare_prices,
    delivery_days,
    summarize_price_comparison,
)
from openeuphemia.areas.italy.replication import (
    external_boundary_prices,
    external_capacity_bounds,
    price_mapping,
    rows_for_day,
)

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "italy"


def clear_day(day: str, data: dict[str, pd.DataFrame]) -> dict:
    """Build one delivery day with blocks left to the solver, and clear it."""

    legs = rows_for_day(data["block_orders"], day).copy()
    legs["id"] = legs["block_id"] + "_p" + legs["period"].astype(str)

    capacities = rows_for_day(data["internal_capacities"], day)
    links = sorted({(r.from_zone, r.to_zone) for r in capacities.itertuples()})

    market = PowerMarket(
        name=f"italy-blocks-{day}",
        delivery_day=day,
        zones=list(ITALY_PRICE_AREAS),
        interconnectors=links,
        periods=list(range(1, 25)),
        block_orders=legs[
            ["id", "block_id", "period", "zone", "side", "quantity_mwh", "price_eur_per_mwh"]
        ],
    )
    for (period, zone), sides in bid_curves_from_table(
        rows_for_day(data["simple_curves"], day)
    ).items():
        market.add_bid_curve(zone=zone, period=period, **sides)
    for row in capacities.itertuples():
        market.set_ntc(
            row.from_zone, row.to_zone, period=row.period,
            forward_capacity_mwh=row.forward_capacity_mwh,
            reverse_capacity_mwh=row.reverse_capacity_mwh,
        )

    published = price_mapping(data["published_prices"], delivery_day=day)
    border, _ = external_boundary_prices(
        external_capacity_bounds(
            data["transfer_capacities"], delivery_day=day, zones=ITALY_PRICE_AREAS
        ),
        {key: value for key, value in published.items() if key[1] not in ITALY_PRICE_AREAS},
    )
    for row in border.itertuples():
        market.add_fixed_price_boundary(
            id=row.id, period=row.period, zone=row.zone, external_zone=row.external_zone,
            price_eur_per_mwh=row.price_eur_per_mwh,
            import_capacity_mwh=row.import_capacity_mwh,
            export_capacity_mwh=row.export_capacity_mwh,
        )

    result = market.clear(method="full-milp")

    comparison = compare_prices(
        result.prices,
        {key: value for key, value in published.items() if key[1] in ITALY_PRICE_AREAS},
        delivery_day=day,
    )

    accepted = result.accepted_orders[result.accepted_orders["order_type"] == "block"]
    ratio = (
        accepted.groupby("block_id")["accepted_mwh"].sum()
        / legs.groupby("block_id")["quantity_mwh"].sum()
    )
    decisions = pd.DataFrame({
        "published_status": legs.groupby("block_id")["published_status"].first(),
        "solver_accepted": (ratio > 0.5).reindex(ratio.index, fill_value=False),
    })
    decisions["published_accepted"] = decisions["published_status"] == "ACC"
    decisions["match"] = decisions["published_accepted"] == decisions["solver_accepted"]
    decisions.insert(0, "delivery_day", day)

    return {"prices": comparison, "blocks": decisions.reset_index()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", default="2025-04")
    parser.add_argument("--day", default=None, help="clear a single delivery day")
    parser.add_argument("--data-root", default=str(DATA_ROOT))
    args = parser.parse_args()

    root = Path(args.data_root) / args.month
    data = {
        "simple_curves": pd.read_csv(root / "simple-bid-curves.csv.gz"),
        "block_orders": pd.read_csv(root / "block-orders.csv.gz"),
        "transfer_capacities": pd.read_csv(root / "transfer-capacities.csv.gz"),
        "internal_capacities": pd.read_csv(root / "internal-transfer-capacities.csv.gz"),
        "published_prices": pd.read_csv(root / "published-prices.csv.gz"),
    }

    days = [args.day] if args.day else delivery_days(data["simple_curves"])
    price_frames, block_frames = [], []
    for day in days:
        outcome = clear_day(day, data)
        price_frames.append(outcome["prices"])
        block_frames.append(outcome["blocks"])
        stats = summarize_price_comparison(outcome["prices"])
        blocks = outcome["blocks"]
        print(
            f"{day}  price MAE {stats['price_mae_eur_per_mwh']:.4f}  "
            f"exact {stats['exact_rows']}/{stats['matched_rows']}  "
            f"blocks {int(blocks['match'].sum())}/{len(blocks)}"
        )

    prices = pd.concat(price_frames, ignore_index=True)
    blocks = pd.concat(block_frames, ignore_index=True)
    total = summarize_price_comparison(prices)

    print(f"\nTOTAL  price MAE {total['price_mae_eur_per_mwh']:.4f}  "
          f"max {total['price_max_abs_error_eur_per_mwh']:.4f}  "
          f"exact {total['exact_rows']}/{total['matched_rows']}")
    print(f"       blocks matched {int(blocks['match'].sum())}/{len(blocks)}")
    print("\nBlock decisions by published status:")
    by_status = blocks.groupby("published_status").agg(
        blocks=("match", "size"), matched=("match", "sum")
    )
    by_status["matched"] = by_status["matched"].astype(int)
    print(by_status.to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
