"""OpenEUPHEMIA: an open-source replication of EUPHEMIA day-ahead market clearing.

The long-term aim is a full open-source EUPHEMIA algorithm for the whole
SDAC region. The current milestone replicates the published Italian
day-ahead zonal prices exactly from public GME data alone.
"""

from __future__ import annotations

from openeuphemia.core import (
    ExternalZone,
    Interconnector,
    MarketClearingResult,
    PowerMarket,
    PriceZone,
)
from openeuphemia.curves import BidCurve, bid_curves_from_table

__version__ = "0.1.0"

__all__ = [
    "BidCurve",
    "ExternalZone",
    "Interconnector",
    "MarketClearingResult",
    "PowerMarket",
    "PriceZone",
    "__version__",
    "bid_curves_from_table",
]
