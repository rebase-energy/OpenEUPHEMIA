"""OpenEUPHEMIA: an open-source replication of EUPHEMIA day-ahead market clearing.

The long-term aim is a full open-source EUPHEMIA algorithm for the whole
SDAC region. The current milestone replicates the published Italian
day-ahead zonal prices exactly from public GME data alone.
"""

from __future__ import annotations

from openeuphemia.core import Market, MarketClearingResult
from openeuphemia.curves import BidCurve, bid_curves_from_table
from openeuphemia.system import System

__version__ = "0.1.0"

__all__ = [
    "BidCurve",
    "Market",
    "MarketClearingResult",
    "System",
    "__version__",
    "bid_curves_from_table",
]
