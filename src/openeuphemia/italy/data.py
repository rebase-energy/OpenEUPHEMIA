"""Fetch-and-cache helpers for the public GME inputs of the Italy replication.

Everything the replication needs is downloaded from GME's public website
and cached locally so a delivery day is only fetched once:

- the public order book (*offerte pubbliche*), published with a roughly
  one-week delay;
- the *Limiti di transito* transfer-capacity limits;
- the published MGP zonal prices (*Prezzi*).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import pandas as pd

from openeuphemia.gme.offers import GmePublicOffersClient, parse_mgp_offers_zip
from openeuphemia.gme.prices import GmeMgpPriceClient, load_or_fetch_mgp_prices
from openeuphemia.gme.transits import (
    GmeTransitsClient,
    capacity_bounds_from_limits,
    fetch_day_bundle,
    normalize_transit_bundle,
)

DEFAULT_CACHE_ROOT = Path("data/gme")


def load_or_fetch_offers(
    delivery_day: str | date,
    *,
    cache_root: str | Path = DEFAULT_CACHE_ROOT,
    refresh: bool = False,
    client: GmePublicOffersClient | None = None,
) -> pd.DataFrame:
    """Load the processed public order book for one day, downloading if needed."""

    day = _coerce_day(delivery_day)
    cache_path = (
        Path(cache_root)
        / "mgp-offers"
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"offers-{day.isoformat()}.csv.gz"
    )
    if cache_path.exists() and not refresh:
        return pd.read_csv(cache_path, compression="gzip", low_memory=False)
    downloader = client or GmePublicOffersClient()
    payload = downloader.download_day(day)
    frame = parse_mgp_offers_zip(payload, delivery_day=day)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(cache_path, index=False, compression="gzip")
    return frame


def load_or_fetch_capacity_bounds(
    delivery_day: str | date,
    *,
    cache_root: str | Path = DEFAULT_CACHE_ROOT,
    refresh: bool = False,
    client: GmeTransitsClient | None = None,
) -> pd.DataFrame:
    """Load the paired transfer-capacity bounds for all edges of one day."""

    day = _coerce_day(delivery_day)
    cache_path = (
        Path(cache_root)
        / "mgp-capacity-bounds"
        / f"{day.year:04d}"
        / f"{day.month:02d}"
        / f"capacity-bounds-{day.isoformat()}.csv"
    )
    if cache_path.exists() and not refresh:
        return pd.read_csv(cache_path)
    downloader = client or GmeTransitsClient()
    bundle = fetch_day_bundle(downloader, delivery_day=day, typologies=("Limiti",))
    bounds = capacity_bounds_from_limits(normalize_transit_bundle(bundle))
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    bounds.to_csv(cache_path, index=False)
    return bounds


def load_or_fetch_prices(
    delivery_day: str | date,
    *,
    cache_root: str | Path = DEFAULT_CACHE_ROOT,
    refresh: bool = False,
    client: GmeMgpPriceClient | None = None,
) -> pd.DataFrame:
    """Load the published MGP zonal prices for one day, downloading if needed."""

    return load_or_fetch_mgp_prices(
        _coerce_day(delivery_day),
        cache_root=Path(cache_root) / "mgp-prices",
        refresh=refresh,
        client=client,
    )


def iter_days(start_day: str | date, end_day: str | date) -> Iterator[date]:
    """Yield every delivery day from ``start_day`` through ``end_day``."""

    current = _coerce_day(start_day)
    last = _coerce_day(end_day)
    while current <= last:
        yield current
        current += timedelta(days=1)


def _coerce_day(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))
