"""GME MGP transit and transmission-limit helpers."""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

SESSION_URL = (
    "https://gme.mercatoelettrico.org/en-us/Home/Results/Electricity/MGP/"
    "Results/Transits"
)
API_ROOT = "https://gme.mercatoelettrico.org/DesktopModules/GmeEsitiTransiti/API"
USER_AGENT = "OpenEUPHEMIA GME MGP transits downloader"
GME_WINDOW_NAME = "GmeEsitiTransitiME"
DEFAULT_TYPOLOGIES = ("Limiti", "Transito")
ITALY_PHYSICAL_ZONES = ("NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD")


@dataclass(frozen=True)
class TransitDownloadSummary:
    delivery_day: str
    raw_output: str
    normalized_output: str
    capacity_bounds_output: str
    internal_capacity_bounds_output: str
    realized_transits_output: str
    scheduled_flows_output: str
    status: str
    typologies: int = 0
    edges: int = 0
    records: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GmeTransitsClient:
    """Client for the JSON API behind the GME MGP Transits page."""

    def __init__(self, *, attempts: int = 4, timeout: int = 90) -> None:
        self.attempts = attempts
        self.timeout = timeout
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.module_id = "8570"
        self.tab_id = "1553"
        self.token = ""
        self.refresh_session()

    def refresh_session(self) -> None:
        request = urllib.request.Request(
            SESSION_URL,
            headers={
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
                ),
                "user-agent": USER_AGENT,
            },
        )
        errors: list[str] = []
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
                        "failed to refresh GME session after "
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

    def get_edges(
        self,
        *,
        delivery_day: date,
        end_day: date | None = None,
        typology: str,
        market: str = "MGP",
        language: str = "en-US",
    ) -> list[dict[str, Any]]:
        final_day = delivery_day if end_day is None else end_day
        payload = self._get_json(
            "item/GetMELista",
            {
                "dataInizio": _compact_date(delivery_day),
                "dataFine": _compact_date(final_day),
                "mercato": market,
                "tipologia": typology,
                "lingua": language,
            },
        )
        if not isinstance(payload, list):
            raise RuntimeError(f"unexpected GetMELista payload: {payload!r}")
        return payload

    def get_values(
        self,
        *,
        delivery_day: date,
        end_day: date | None = None,
        edge_code: str,
        typology: str,
        market: str = "MGP",
    ) -> dict[str, Any]:
        final_day = delivery_day if end_day is None else end_day
        payload = self._get_json(
            "item/GetValueGraph",
            {
                "dataInizio": _compact_date(delivery_day),
                "dataFine": _compact_date(final_day),
                "zona": edge_code,
                "mercato": market,
                "tipologia": typology,
            },
        )
        if not isinstance(payload, dict):
            raise RuntimeError(f"unexpected GetValueGraph payload: {payload!r}")
        return payload

    def _get_json(self, path: str, params: Mapping[str, str]) -> Any:
        url = f"{API_ROOT}/{path}?{urllib.parse.urlencode(params)}"
        errors: list[str] = []
        refreshed = False
        for attempt in range(1, self.attempts + 1):
            try:
                request = urllib.request.Request(url, headers=self._api_headers())
                with self.opener.open(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
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
                json.JSONDecodeError,
            ) as exc:
                errors.append(str(exc))
                if attempt == self.attempts or not _looks_retryable(exc):
                    break
            time.sleep(min(2 ** (attempt - 1), 8))
        raise RuntimeError(
            f"failed to fetch {path} after {len(errors)} attempt(s): {errors[-1]}"
        )

    def _api_headers(self) -> dict[str, str]:
        return {
            "accept": "application/json, text/plain, */*",
            "referer": SESSION_URL,
            "user-agent": USER_AGENT,
            "x-requested-with": "XMLHttpRequest",
            "ModuleId": self.module_id,
            "TabId": self.tab_id,
            "RequestVerificationToken": self.token,
        }


def fetch_day_bundle(
    client: GmeTransitsClient,
    *,
    delivery_day: date,
    typologies: Sequence[str] = DEFAULT_TYPOLOGIES,
    market: str = "MGP",
    language: str = "en-US",
    request_sleep: float = 0.1,
) -> dict[str, Any]:
    return fetch_range_bundle(
        client,
        start_day=delivery_day,
        end_day=delivery_day,
        typologies=typologies,
        market=market,
        language=language,
        request_sleep=request_sleep,
    )


def fetch_range_bundle(
    client: GmeTransitsClient,
    *,
    start_day: date,
    end_day: date,
    typologies: Sequence[str] = DEFAULT_TYPOLOGIES,
    market: str = "MGP",
    language: str = "en-US",
    request_sleep: float = 0.1,
) -> dict[str, Any]:
    if start_day > end_day:
        raise ValueError("start_day must be on or before end_day")
    typology_payloads = []
    for typology in typologies:
        edges = client.get_edges(
            delivery_day=start_day,
            end_day=end_day,
            typology=typology,
            market=market,
            language=language,
        )
        series = []
        for edge_index, edge in enumerate(edges):
            edge_code = str(edge["codice"])
            values = client.get_values(
                delivery_day=start_day,
                end_day=end_day,
                edge_code=edge_code,
                typology=typology,
                market=market,
            )
            series.append({"edge": edge, "values": values})
            if request_sleep > 0 and edge_index != len(edges) - 1:
                time.sleep(request_sleep)
        typology_payloads.append({"typology": typology, "edges": edges, "series": series})
    return {
        "source": "gme-mgp-transits-api",
        "market": market,
        "delivery_date": start_day.isoformat(),
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "date": _compact_date(start_day),
        "date_start": _compact_date(start_day),
        "date_end": _compact_date(end_day),
        "typologies": typology_payloads,
        "fetched_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def normalize_transit_bundle(bundle: Mapping[str, Any]) -> pd.DataFrame:
    """Flatten a GME transits bundle to one row per edge-period value."""

    rows: list[dict[str, Any]] = []
    bundle_day = str(bundle.get("delivery_date") or bundle.get("start_date") or "")
    market = str(bundle.get("market", "MGP"))
    for typology_payload in bundle.get("typologies", ()):
        typology = str(typology_payload["typology"])
        for series in typology_payload.get("series", ()):
            edge = series["edge"]
            edge_code = str(edge["codice"])
            from_zone, to_zone = split_edge_code(edge_code)
            unit = _unit_from_values(series.get("values", {}))
            for record in series.get("values", {}).get("Volumi", ()):
                delivery_day = _record_delivery_day(record, default_day=bundle_day)
                hour = int(record.get("h") or 0)
                quarter_hour = int(record.get("qh") or 0)
                rows.append(
                    {
                        "delivery_day": delivery_day,
                        "market": market,
                        "typology": typology,
                        "edge_code": edge_code,
                        "edge_name": str(edge.get("nome", "")),
                        "edge_type": str(edge.get("tipo", "")),
                        "from_zone": from_zone,
                        "to_zone": to_zone,
                        "period": quarter_hour if quarter_hour > 0 else hour,
                        "hour": hour,
                        "quarter_hour": quarter_hour,
                        "value_mwh": float(record["p"]),
                        "unit": unit,
                    }
                )
    return pd.DataFrame(
        rows,
        columns=[
            "delivery_day",
            "market",
            "typology",
            "edge_code",
            "edge_name",
            "edge_type",
            "from_zone",
            "to_zone",
            "period",
            "hour",
            "quarter_hour",
            "value_mwh",
            "unit",
        ],
    )


def capacity_bounds_from_limits(
    normalized: pd.DataFrame,
    *,
    zones: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Convert directional ``Limiti`` rows to min/max flow bounds.

    The returned flow convention is export-positive from ``from_zone`` to
    ``to_zone``. For paired limits A-B and B-A, max is A->B and min is -B->A.
    """

    if normalized.empty:
        return pd.DataFrame(columns=_CAPACITY_BOUND_COLUMNS)
    zone_set = None if zones is None else {str(zone).upper() for zone in zones}
    limits = normalized[normalized["typology"].str.lower() == "limiti"].copy()
    if zone_set is not None:
        limits = limits[
            limits["from_zone"].isin(zone_set) & limits["to_zone"].isin(zone_set)
        ].copy()
    if limits.empty:
        return pd.DataFrame(columns=_CAPACITY_BOUND_COLUMNS)

    values = {
        (
            str(row.delivery_day),
            int(row.period),
            str(row.from_zone),
            str(row.to_zone),
        ): float(row.value_mwh)
        for row in limits.itertuples(index=False)
    }
    pair_keys = sorted(
        {
            (delivery_day, period, *sorted((from_zone, to_zone)))
            for delivery_day, period, from_zone, to_zone in values
        }
    )
    rows = []
    for delivery_day, period, first, second in pair_keys:
        forward = values.get((delivery_day, period, first, second))
        reverse = values.get((delivery_day, period, second, first))
        if forward is None and reverse is None:
            continue
        max_flow = forward if forward is not None else 0.0
        reverse_limit = reverse if reverse is not None else 0.0
        rows.append(
            {
                "delivery_day": delivery_day,
                "period": period,
                "id": f"{first}_{second}",
                "from_zone": first,
                "to_zone": second,
                "min_flow_mwh": -float(reverse_limit),
                "max_flow_mwh": float(max_flow),
                "forward_edge_code": f"{first}-{second}"
                if forward is not None
                else None,
                "reverse_edge_code": f"{second}-{first}"
                if reverse is not None
                else None,
                "has_forward_limit": bool(forward is not None),
                "has_reverse_limit": bool(reverse is not None),
                "source": "gme-mgp-transits-limiti",
            }
        )
    return pd.DataFrame(rows, columns=_CAPACITY_BOUND_COLUMNS)


def scheduled_flow_net_positions(
    scheduled_flows: pd.DataFrame,
    *,
    zones: Iterable[str],
    delivery_day: str | date | None = None,
) -> dict[tuple[int, str], float]:
    """Aggregate signed GME ``Transito`` rows to zone net-export targets.

    ``value_mwh`` is interpreted as signed flow in the published edge direction,
    so positive A-B contributes export from A and import into B. Edges to zones
    outside ``zones`` are retained as external boundary flows for the in-scope
    endpoint.
    """

    zone_set = {str(zone).upper() for zone in zones}
    if scheduled_flows.empty or not zone_set:
        return {}
    frame = scheduled_flows.copy()
    if delivery_day is not None and "delivery_day" in frame.columns:
        frame = frame[frame["delivery_day"].astype(str) == str(delivery_day)]
    if "typology" in frame.columns:
        frame = frame[frame["typology"].astype(str).str.lower() == "transito"]
    required = {"period", "from_zone", "to_zone", "value_mwh"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"scheduled flow frame is missing columns: {sorted(missing)}")

    net_positions: dict[tuple[int, str], float] = {}
    for row in frame.itertuples(index=False):
        period = int(getattr(row, "period"))
        from_zone = str(getattr(row, "from_zone")).upper()
        to_zone = str(getattr(row, "to_zone")).upper()
        value = float(getattr(row, "value_mwh"))
        if from_zone in zone_set:
            key = (period, from_zone)
            net_positions[key] = net_positions.get(key, 0.0) + value
        if to_zone in zone_set:
            key = (period, to_zone)
            net_positions[key] = net_positions.get(key, 0.0) - value
    return net_positions


def split_edge_code(edge_code: str) -> tuple[str, str]:
    parts = str(edge_code).upper().split("-")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"unexpected GME edge code: {edge_code!r}")
    return parts[0], parts[1]


def compact_date(value: date) -> str:
    return _compact_date(value)


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


_CAPACITY_BOUND_COLUMNS = [
    "delivery_day",
    "period",
    "id",
    "from_zone",
    "to_zone",
    "min_flow_mwh",
    "max_flow_mwh",
    "forward_edge_code",
    "reverse_edge_code",
    "has_forward_limit",
    "has_reverse_limit",
    "source",
]


def _unit_from_values(values: Mapping[str, Any]) -> str | None:
    units = values.get("UnitaMisura")
    if not isinstance(units, list) or not units:
        return None
    first = units[0]
    if not isinstance(first, Mapping):
        return None
    unit = first.get("Unita")
    return None if unit is None else str(unit)


def _record_delivery_day(record: Mapping[str, Any], *, default_day: str) -> str:
    raw_date = record.get("df")
    if raw_date not in (None, ""):
        compact = str(int(raw_date))
        if len(compact) == 8:
            return f"{compact[:4]}-{compact[4:6]}-{compact[6:8]}"
    if default_day:
        return default_day
    raise ValueError(f"record has no delivery date: {record!r}")


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


def _compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")
