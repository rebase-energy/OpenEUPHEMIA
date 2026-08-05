"""Replicate the published Italian day-ahead results for a committed month.

Reads the validation inputs committed under ``data/italy`` and clears
every delivery day they contain.

Prices are validated with the price-taking boundary (``--boundary prices``),
flows with the published cross-border exchanges imposed
(``--boundary exchanges``) plus a flow-selection rule.

Examples::

    python scripts/replicate_italy_prices.py
    python scripts/replicate_italy_prices.py --day 2025-04-01
    python scripts/replicate_italy_prices.py --boundary exchanges --flow-selection pro-rata
    python scripts/replicate_italy_prices.py --output-csv comparison.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from openeuphemia.areas.italy import (
    delivery_days,
    replicate_italy_day,
    summarize_flow_comparison,
    summarize_price_comparison,
)

DATA_ROOT = Path(__file__).resolve().parents[1] / "data" / "italy"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--month", default="2025-04", help="committed month to replicate")
    parser.add_argument("--day", default=None, help="replicate a single delivery day")
    parser.add_argument(
        "--boundary",
        default="prices",
        choices=("prices", "exchanges"),
        help="how to close the model at the border",
    )
    parser.add_argument(
        "--flow-selection",
        default=None,
        choices=("volume-max", "pro-rata", "anchored"),
        help="rule resolving which welfare-equal flow pattern is returned",
    )
    parser.add_argument("--data-root", default=str(DATA_ROOT))
    parser.add_argument("--output-csv", default=None)
    args = parser.parse_args()

    root = Path(args.data_root) / args.month
    data = {
        name: pd.read_csv(root / f"{name.replace('_', '-')}.csv.gz")
        for name in (
            "bid_curves",
            "transfer_capacities",
            "published_prices",
            "published_flows",
            "published_exchanges",
        )
    }
    flows_wanted = args.boundary == "exchanges"

    days = [args.day] if args.day else delivery_days(data["bid_curves"])
    price_frames: list[pd.DataFrame] = []
    flow_frames: list[pd.DataFrame] = []
    for day in days:
        result = replicate_italy_day(
            delivery_day=day,
            boundary=args.boundary,
            flow_selection=args.flow_selection,
            **data,
        )
        summary = result.summary
        price_frames.append(result.price_comparison)
        flow_frames.append(result.flow_comparison)
        if flows_wanted:
            print(
                f"{day}  flow MAE {summary['flow_mae_mwh']:.4f}  "
                f"max {summary['flow_max_abs_error_mwh']:.3f}  "
                f"exact {summary['exact_flow_rows']}/{summary['flow_rows']}"
            )
        else:
            print(
                f"{day}  MAE {summary['price_mae_eur_per_mwh']:.4f}  "
                f"max {summary['price_max_abs_error_eur_per_mwh']:.3f}  "
                f"exact {summary['exact_rows']}/{summary['matched_rows']}  "
                f"dropped-borders {summary['dropped_unpriced_borders']}"
            )

    if flows_wanted:
        combined = pd.concat(flow_frames, ignore_index=True)
        total = summarize_flow_comparison(combined)
        print(
            f"\nTOTAL  flow MAE {total['flow_mae_mwh']:.4f}  "
            f"max {total['flow_max_abs_error_mwh']:.3f}  "
            f"exact {total['exact_flow_rows']}/{total['flow_rows']}"
        )
    else:
        combined = pd.concat(price_frames, ignore_index=True)
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
