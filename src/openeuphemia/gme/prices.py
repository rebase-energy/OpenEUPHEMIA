"""GME MGP zonal price download helpers."""

from __future__ import annotations

import http.client
import http.cookiejar
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree

import pandas as pd

PRICE_DOWNLOAD_URL = (
    "https://www.mercatoelettrico.org/en-us/Home/Results/Electricity/MGP/"
    "Download/Download?valore=Prezzi"
)
DOWNLOAD_API_ROOT = "https://www.mercatoelettrico.org/DesktopModules/GmeDownload/API"
USER_AGENT = "OpenEUPHEMIA GME MGP prices downloader"
GME_WINDOW_NAME = "GmeDownload"
DEFAULT_MGP_PRICE_CACHE_ROOT = Path("data/raw/gme/mgp-prices")


class GmeMgpPriceClient:
    """Client for the GME download endpoint behind the MGP ``Prezzi`` page."""

    def __init__(self, *, attempts: int = 4, timeout: int = 90) -> None:
        self.attempts = attempts
        self.timeout = timeout
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.module_id = "12103"
        self.tab_id = "1749"
        self.token = ""
        self.refresh_session()

    def refresh_session(self) -> None:
        request = urllib.request.Request(
            PRICE_DOWNLOAD_URL,
            headers={
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "user-agent": USER_AGENT,
            },
        )
        errors: list[str] = []
        page = ""
        for attempt in range(1, self.attempts + 1):
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    page = response.read().decode("utf-8", errors="replace")
                break
            except (
                http.client.RemoteDisconnected,
                TimeoutError,
                urllib.error.HTTPError,
                urllib.error.URLError,
            ) as exc:
                errors.append(str(exc))
                if attempt == self.attempts or not _looks_retryable(exc):
                    raise RuntimeError(
                        "failed to refresh GME price session after "
                        f"{len(errors)} attempt(s): {errors[-1]}"
                    ) from exc
                time.sleep(min(2 ** (attempt - 1), 8))

        tokens = re.findall(
            r'__RequestVerificationToken"\s+type="hidden"\s+value="([^"]+)"',
            page,
        )
        if not tokens:
            raise RuntimeError("could not find GME request verification token")
        self.token = tokens[-1]

        context = _window_context(page)
        if context:
            self.module_id = str(context.get("ModuleId") or self.module_id)
            self.tab_id = str(context.get("TabId") or self.tab_id)

    def download_price_zip(
        self,
        *,
        delivery_day: date,
        market: str = "MGP",
    ) -> bytes:
        compact_day = _compact_date(delivery_day)
        params = urllib.parse.urlencode(
            {
                "DataInizio": compact_day,
                "DataFine": compact_day,
                "Date": compact_day,
                "Mercato": market,
                "Settore": "Prezzi",
                "FiltroDate": "InizioFine",
            }
        )
        url = f"{DOWNLOAD_API_ROOT}/ExcelDownload/downloadzipfile?{params}"
        errors: list[str] = []
        refreshed = False
        for attempt in range(1, self.attempts + 1):
            try:
                request = urllib.request.Request(url, headers=self._api_headers())
                with self.opener.open(request, timeout=self.timeout) as response:
                    return response.read()
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace").strip()
                errors.append(f"HTTP {exc.code}: {detail[:300]}")
                if exc.code in (401, 403) and not refreshed:
                    self.refresh_session()
                    refreshed = True
                    continue
                if attempt == self.attempts or not _looks_retryable(exc):
                    break
            except (
                http.client.RemoteDisconnected,
                TimeoutError,
                urllib.error.URLError,
            ) as exc:
                errors.append(str(exc))
                if attempt == self.attempts or not _looks_retryable(exc):
                    break
            time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(
            "failed to download GME MGP prices after "
            f"{len(errors)} attempt(s): {errors[-1]}"
        )

    def _api_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "referer": PRICE_DOWNLOAD_URL,
            "user-agent": USER_AGENT,
            "x-requested-with": "XMLHttpRequest",
            "ModuleId": self.module_id,
            "TabId": self.tab_id,
            "RequestVerificationToken": self.token,
        }


def load_or_fetch_mgp_prices(
    delivery_day: str | date,
    *,
    cache_root: str | Path = DEFAULT_MGP_PRICE_CACHE_ROOT,
    refresh: bool = False,
    client: GmeMgpPriceClient | None = None,
    market: str = "MGP",
) -> pd.DataFrame:
    """Load a cached GME MGP price zip or download it from GME."""

    day = _coerce_date(delivery_day)
    cache_path = _cache_path(cache_root, day, market=market)
    if cache_path.exists() and not refresh:
        return load_mgp_prices(cache_path)
    downloader = client or GmeMgpPriceClient()
    payload = downloader.download_price_zip(delivery_day=day, market=market)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_bytes(payload)
    return parse_mgp_price_zip(payload)


def load_mgp_prices(path: str | Path) -> pd.DataFrame:
    """Load GME MGP prices from a downloaded zip or raw XML file."""

    source = Path(path)
    data = source.read_bytes()
    if zipfile.is_zipfile(source):
        return parse_mgp_price_zip(data)
    return parse_mgp_price_xml(data)


def parse_mgp_price_zip(data: bytes) -> pd.DataFrame:
    """Parse the GME MGP ``Prezzi`` zip payload to long-form prices."""

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        if not xml_names:
            raise ValueError("GME MGP price zip contains no XML file")
        return parse_mgp_price_xml(archive.read(xml_names[0]))


def parse_mgp_price_xml(data: bytes | str) -> pd.DataFrame:
    """Parse GME MGP ``Prezzi`` XML into one row per period-zone price."""

    payload = data.encode("utf-8") if isinstance(data, str) else data
    root = ElementTree.fromstring(payload)
    rows: list[dict[str, object]] = []
    for node in root.findall("Prezzi"):
        raw_day = node.findtext("Data")
        raw_period = node.findtext("Ora")
        if raw_day is None or raw_period is None:
            continue
        delivery_day = _iso_date_from_compact(raw_day)
        period = int(raw_period)
        market = node.findtext("Mercato") or "MGP"
        for child in node:
            if child.tag in {"Data", "Mercato", "Ora"}:
                continue
            if child.text in (None, ""):
                continue
            rows.append(
                {
                    "delivery_day": delivery_day,
                    "market": market,
                    "period": period,
                    "zone": str(child.tag).upper(),
                    "price_eur_per_mwh": float(_decimal_text(child.text)),
                    "source": "gme-mgp-prezzi",
                }
            )
    return pd.DataFrame(
        rows,
        columns=[
            "delivery_day",
            "market",
            "period",
            "zone",
            "price_eur_per_mwh",
            "source",
        ],
    )


def mgp_price_mapping(
    prices: pd.DataFrame,
    *,
    delivery_day: str | date | None = None,
    zones: Iterable[str] | None = None,
) -> dict[tuple[int, str], float]:
    """Convert a long-form GME price frame to ``(period, zone) -> price``."""

    if prices.empty:
        return {}
    frame = prices.copy()
    if delivery_day is not None and "delivery_day" in frame.columns:
        day = _coerce_date(delivery_day).isoformat()
        frame = frame[frame["delivery_day"].astype(str) == day]
    if zones is not None:
        zone_set = {str(zone).upper() for zone in zones}
        frame = frame[frame["zone"].astype(str).str.upper().isin(zone_set)]
    if frame.empty:
        return {}
    frame = frame.assign(zone=lambda value: value["zone"].astype(str).str.upper())
    grouped = frame.groupby(["period", "zone"], dropna=False)["price_eur_per_mwh"].last()
    return {
        (int(period), str(zone)): float(price)
        for (period, zone), price in grouped.items()
        if pd.notna(price)
    }


def _cache_path(cache_root: str | Path, delivery_day: date, *, market: str) -> Path:
    compact_day = _compact_date(delivery_day)
    return (
        Path(cache_root)
        / f"date={delivery_day.isoformat()}"
        / f"{market}_Prezzi{compact_day}.zip"
    )


def _decimal_text(value: str) -> Decimal:
    return Decimal(str(value).strip().replace(",", "."))


def _iso_date_from_compact(value: str) -> str:
    compact = str(value).strip()
    if len(compact) != 8:
        raise ValueError(f"unexpected GME date value: {value!r}")
    return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"


def _coerce_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _window_context(page: str) -> dict[str, Any] | None:
    pattern = rf'<script>window\["{re.escape(GME_WINDOW_NAME)}"\]\s*=\s*(\{{.*?\}})</script>'
    match = re.search(pattern, page, flags=re.DOTALL)
    if not match:
        return None
    return json.loads(match.group(1))


def _looks_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    return isinstance(
        exc,
        (http.client.RemoteDisconnected, TimeoutError, urllib.error.URLError),
    )

