"""Replicate published Italian day-ahead zonal prices for a range of days.

Downloads all required public GME data (order book, transfer-capacity
limits, published prices) on first use and caches it under ``data/gme``.

Examples::

    python scripts/replicate_italy_prices.py 2025-04-01
    python scripts/replicate_italy_prices.py 2025-04-01 2025-04-30
    python scripts/replicate_italy_prices.py 2025-04-01 2025-04-30 \
        --output-csv april-2025-price-comparison.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openeuphemia.italy.data import (
    DEFAULT_CACHE_ROOT,
    iter_days,
    load_or_fetch_capacity_bounds,
    load_or_fetch_offers,
    load_or_fetch_prices,
)
from openeuphemia.italy.replication import (
    replicate_italy_day,
    summarize_price_comparison,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start_day", help="first delivery day (YYYY-MM-DD)")
    parser.add_argument(
        "end_day",
        nargs="?",
        help="last delivery day (default: same as start_day)",
    )
    parser.add_argument(
        "--cache-root",
        default=str(DEFAULT_CACHE_ROOT),
        help="local cache directory for downloaded GME data",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="write the per-(period, zone) price comparison to this CSV",
    )
    args = parser.parse_args()

    end_day = args.end_day or args.start_day
    comparisons: list[pd.DataFrame] = []
    for day in iter_days(args.start_day, end_day):
        offers = load_or_fetch_offers(day, cache_root=args.cache_root)
        bounds = load_or_fetch_capacity_bounds(day, cache_root=args.cache_root)
        prices = load_or_fetch_prices(day, cache_root=args.cache_root)
        result = replicate_italy_day(
            delivery_day=day,
            offers=offers,
            capacity_bounds=bounds,
            reference_prices=prices,
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
