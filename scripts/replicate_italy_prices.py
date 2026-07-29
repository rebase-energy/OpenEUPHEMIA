"""Replicate the published Italian day-ahead zonal prices for a committed month.

Reads the validation inputs committed under ``data/italy`` and clears
every delivery day they contain.

Examples::

    python scripts/replicate_italy_prices.py
    python scripts/replicate_italy_prices.py --month 2025-04
    python scripts/replicate_italy_prices.py --day 2025-04-01
    python scripts/replicate_italy_prices.py --output-csv price-comparison.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openeuphemia.italy import (
    delivery_days,
    replicate_italy_day,
    summarize_price_comparison,
)

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "italy"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", default="2025-04", help="committed month to replicate")
    parser.add_argument("--day", default=None, help="replicate a single delivery day")
    parser.add_argument("--data-root", default=str(DATA_ROOT))
    parser.add_argument("--output-csv", default=None)
    args = parser.parse_args()

    root = Path(args.data_root) / args.month
    bid_curves = pd.read_csv(root / "bid-curves.csv.gz")
    transfer_capacities = pd.read_csv(root / "transfer-capacities.csv.gz")
    published_prices = pd.read_csv(root / "published-prices.csv.gz")

    days = [args.day] if args.day else delivery_days(bid_curves)
    comparisons: list[pd.DataFrame] = []
    for day in days:
        result = replicate_italy_day(
            delivery_day=day,
            bid_curves=bid_curves,
            transfer_capacities=transfer_capacities,
            published_prices=published_prices,
        )
        summary = result.summary
        comparisons.append(result.price_comparison)
        print(
            f"{day}  MAE {summary['price_mae_eur_per_mwh']:.4f}  "
            f"max {summary['price_max_abs_error_eur_per_mwh']:.3f}  "
            f"exact {summary['exact_rows']}/{summary['matched_rows']}  "
            f"dropped-borders {summary['dropped_unpriced_borders']}"
        )

    combined = pd.concat(comparisons, ignore_index=True)
    total = summarize_price_comparison(combined)
    print(
        f"\nTOTAL  MAE {total['price_mae_eur_per_mwh']:.4f}  "
        f"max {total['price_max_abs_error_eur_per_mwh']:.3f}  "
        f"exact {total['exact_rows']}/{total['matched_rows']}"
    )
    if args.output_csv:
        combined.to_csv(args.output_csv, index=False)
        print(f"wrote {args.output_csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
