"""GME MGP public offers: download and parse the daily order book.

GME publishes the complete day-ahead (MGP) order book — every bid and
offer with unit, zone, price, quantity, and acceptance status — as daily
ZIP files with a roughly one-week delay, freely and without
authentication. ``GmePublicOffersClient`` downloads one day;
``parse_mgp_offers_zip`` turns the contained XML into a typed frame with
one row per ``OfferteOperatori`` record.

Schema notes: until 2024 the delivery period field is ``INTERVAL_NO``;
from 2025 it is ``PERIOD`` (handled transparently), and offers carry an
``OFFER_TYPE`` distinguishing simple hourly offers ("S") from the block
orders ("B") introduced to the MGP in 2025.
"""

from __future__ import annotations

import http.client
import http.cookiejar
import io
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, date, datetime
from typing import Any, Iterable
from xml.etree import ElementTree

import pandas as pd

SESSION_URL = (
    "https://www.mercatoelettrico.org/en-us/Home/Results/Electricity/MGP/"
    "Download/Download?valore=OffertePubbliche"
)
DOWNLOAD_URL = (
    "https://www.mercatoelettrico.org/DesktopModules/GmeDownload/API/ExcelDownload/"
    "downloadzipfile"
)
USER_AGENT = "OpenEUPHEMIA GME MGP public offers client"
PUBLIC_LAG_DAYS = 7

XML_ROW_TAG = "OfferteOperatori"

STRING_RENAMES = {
    "PURPOSE_CD": "purpose_cd",
    "TYPE_CD": "type_cd",
    "STATUS_CD": "status_cd",
    "MARKET_CD": "market_cd",
    "UNIT_REFERENCE_NO": "unit_reference_no",
    "MARKET_PARTECIPANT_XREF_NO": "market_participant_xref_no",
    "TRANSACTION_REFERENCE_NO": "transaction_reference_no",
    "BALANCED_REFERENCE_NO": "balanced_reference_no",
    "PARTIAL_QTY_ACCEPTED_IN": "partial_qty_accepted_in",
    "GRID_SUPPLY_POINT_NO": "grid_supply_point_no",
    "ZONE_CD": "zone_cd",
    "OPERATORE": "operator",
    "SUBMITTED_DT": "submitted_at_raw",
    "OFFER_TYPE": "offer_type",
    "GRANULARITY": "granularity",
}

NUMERIC_RENAMES = {
    "INTERVAL_NO": "interval_no",
    "QUANTITY_NO": "quantity_mw",
    "AWARDED_QUANTITY_NO": "awarded_quantity_mw",
    "ENERGY_PRICE_NO": "energy_price_eur_per_mwh",
    "MERIT_ORDER_NO": "merit_order_no",
    "ADJ_QUANTITY_NO": "adj_quantity_mw",
    "ADJ_ENERGY_PRICE_NO": "adj_energy_price_eur_per_mwh",
    "AWARDED_PRICE_NO": "awarded_price_eur_per_mwh",
}

PROCESSED_COLUMNS = (
    "delivery_date",
    "purpose_cd",
    "type_cd",
    "status_cd",
    "market_cd",
    "unit_reference_no",
    "market_participant_xref_no",
    "interval_no",
    "transaction_reference_no",
    "balanced_reference_no",
    "quantity_mw",
    "awarded_quantity_mw",
    "energy_price_eur_per_mwh",
    "merit_order_no",
    "partial_qty_accepted_in",
    "adj_quantity_mw",
    "adj_energy_price_eur_per_mwh",
    "grid_supply_point_no",
    "zone_cd",
    "awarded_price_eur_per_mwh",
    "operator",
    "submitted_at_raw",
    "submitted_at",
    "bilateral_in",
    "offer_type",
    "granularity",
    "source_xml_name",
    "processed_at",
)

SIDE_BY_PURPOSE = {"BID": "demand", "OFF": "supply"}
PUBLIC_CURVE_STATUS_CODES = frozenset(("ACC", "REJ"))


class GmePublicOffersClient:
    """Downloads daily MGP public-offer ZIP files from GME's website."""

    def __init__(self, *, attempts: int = 4, timeout: int = 90) -> None:
        self.attempts = attempts
        self.timeout = timeout
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.refresh_session()

    def refresh_session(self) -> None:
        request = urllib.request.Request(
            SESSION_URL,
            headers={
                "accept": "text/html,application/xhtml+xml,application/xml;"
                "q=0.9,*/*;q=0.8",
                "user-agent": USER_AGENT,
            },
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            response.read()

    def download_day(self, delivery_day: date) -> bytes:
        params = {
            "DataInizio": _compact_date(delivery_day),
            "DataFine": _compact_date(delivery_day),
            "Date": _compact_date(delivery_day),
            "Mercato": "MGP",
            "Settore": "OffertePubbliche",
            "FiltroDate": "InizioFine",
        }
        url = f"{DOWNLOAD_URL}?{urllib.parse.urlencode(params)}"
        errors: list[str] = []
        refreshed = False
        for attempt in range(1, self.attempts + 1):
            try:
                request = urllib.request.Request(url, headers=self._headers())
                with self.opener.open(request, timeout=self.timeout) as response:
                    content = response.read()
                _validate_zip(content)
                return content
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")
                errors.append(f"HTTP {exc.code}: {body[:300]}")
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
                zipfile.BadZipFile,
                ValueError,
            ) as exc:
                errors.append(str(exc))
                if attempt == self.attempts:
                    break
            time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(
            f"failed to download public offers for {delivery_day}: {errors[-1]}"
        )

    def _headers(self) -> dict[str, str]:
        return {
            "accept": "application/zip,application/octet-stream,*/*",
            "referer": SESSION_URL,
            "user-agent": USER_AGENT,
            "x-requested-with": "XMLHttpRequest",
            "ModuleId": "12103",
            "TabId": "1749",
        }


def parse_mgp_offers_zip(
    content: bytes,
    *,
    delivery_day: str | date | None = None,
    processed_at: datetime | None = None,
) -> pd.DataFrame:
    """Parse a raw GME MGP public-offers ZIP into typed row-level offer data."""

    processed_timestamp = processed_at or datetime.now(UTC).replace(microsecond=0)
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        xml_names = [
            name for name in archive.namelist() if name.lower().endswith(".xml")
        ]
        if len(xml_names) != 1:
            raise ValueError(
                f"expected exactly one XML file in GME ZIP, found {xml_names}"
            )
        xml_name = xml_names[0]
        with archive.open(xml_name) as xml_handle:
            rows = list(
                _iter_offer_rows(
                    xml_handle,
                    source_xml_name=xml_name,
                    processed_at=processed_timestamp,
                )
            )

    frame = pd.DataFrame(rows, columns=PROCESSED_COLUMNS)
    if frame.empty:
        return frame
    if delivery_day is not None:
        expected = date.fromisoformat(str(delivery_day)).isoformat()
        observed = set(frame["delivery_date"].dropna().unique())
        if observed and observed != {expected}:
            raise ValueError(
                f"GME ZIP delivery dates {sorted(observed)} do not match {expected}"
            )
    return frame


def _iter_offer_rows(
    xml_handle: Any,
    *,
    source_xml_name: str,
    processed_at: datetime,
) -> Iterable[dict[str, Any]]:
    context = ElementTree.iterparse(xml_handle, events=("end",))
    for _, element in context:
        if _local_name(element.tag) != XML_ROW_TAG:
            continue
        raw = {
            _local_name(child.tag): (child.text.strip() if child.text else None)
            for child in list(element)
        }
        yield _normalize_offer_row(
            raw,
            source_xml_name=source_xml_name,
            processed_at=processed_at,
        )
        element.clear()


def _normalize_offer_row(
    raw: dict[str, str | None],
    *,
    source_xml_name: str,
    processed_at: datetime,
) -> dict[str, Any]:
    row: dict[str, Any] = {column: None for column in PROCESSED_COLUMNS}
    bid_offer_date = _parse_gme_date(raw.get("BID_OFFER_DATE_DT"))
    row["delivery_date"] = bid_offer_date.isoformat() if bid_offer_date else None
    for source, target in STRING_RENAMES.items():
        row[target] = raw.get(source)
    for source, target in NUMERIC_RENAMES.items():
        row[target] = _parse_number(raw.get(source))
    if row["interval_no"] is None:
        # The 2025 GME schema renamed INTERVAL_NO to PERIOD.
        row["interval_no"] = _parse_number(raw.get("PERIOD"))
    interval_no = row["interval_no"]
    merit_order_no = row["merit_order_no"]
    row["interval_no"] = int(interval_no) if interval_no is not None else None
    row["merit_order_no"] = (
        int(merit_order_no) if merit_order_no is not None else None
    )
    row["submitted_at"] = _parse_submitted_at(raw.get("SUBMITTED_DT"))
    row["bilateral_in"] = _parse_bool(raw.get("BILATERAL_IN"))
    row["source_xml_name"] = source_xml_name
    row["processed_at"] = processed_at
    return row


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_gme_date(value: str | None) -> date | None:
    if not value:
        return None
    return datetime.strptime(value, "%Y%m%d").date()


def _parse_submitted_at(value: str | None) -> pd.Timestamp | None:
    if not value:
        return None
    parsed = pd.to_datetime(value, format="%Y%m%d%H%M%S%f", errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed


def _parse_number(value: str | None) -> float | None:
    if value in (None, ""):
        return None
    return float(value)


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    return None


def _validate_zip(content: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        names = archive.namelist()
    if not any(name.lower().endswith(".xml") for name in names):
        raise ValueError(f"downloaded ZIP contains no XML file: {names}")


def _compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def _looks_retryable(exc: BaseException) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in {408, 429, 500, 502, 503, 504}
    return isinstance(
        exc, (http.client.RemoteDisconnected, TimeoutError, urllib.error.URLError)
    )
