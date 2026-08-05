"""Replicate published Italian day-ahead (MGP) zonal prices.

The Italian bidding zones are cleared as a welfare-maximizing linear
program built from three tidy input tables:

- aggregated zonal bid curves, one row per price step;
- transfer-capacity bounds for every edge, internal and cross-border;
- published zonal prices, for the Italian zones and their neighbours.

Internal edges become interconnectors. Every external border becomes a
price-taking boundary at the neighbouring zone's published price, bounded
by the published border capacity — a Dirichlet boundary condition. No
observed flows, scheduled exchanges, or tie-break rules enter the model.
Zonal prices are read from the balance-constraint duals.

See ``data/italy`` for the committed inputs and how they are derived from
GME's publications.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from openeuphemia.core import MarketClearingResult, PowerMarket
from openeuphemia.curves import bid_curves_from_table

ITALY_PRICE_AREAS = ("NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD")

PRICE_TOLERANCE_EUR = 0.005
FLOW_TOLERANCE_MWH = 0.01

BOUNDARY_CONDITIONS = ("prices", "exchanges")


@dataclass(frozen=True)
class ItalyMarket:
    """An Italian delivery day built into a clearable :class:`PowerMarket`."""

    market: PowerMarket
    boundary_diagnostics: pd.DataFrame


@dataclass(frozen=True)
class ItalyReplicationResult:
    """Outcome of replicating one delivery day of Italian zonal prices."""

    delivery_day: str
    market: PowerMarket
    clearing: MarketClearingResult
    price_comparison: pd.DataFrame
    flow_comparison: pd.DataFrame
    boundary_diagnostics: pd.DataFrame
    summary: dict[str, Any]


def build_italy_market(
    *,
    delivery_day: str | date,
    bid_curves: pd.DataFrame,
    transfer_capacities: pd.DataFrame,
    published_prices: pd.DataFrame,
    published_exchanges: pd.DataFrame | None = None,
    boundary: str = "prices",
    zones: Sequence[str] = ITALY_PRICE_AREAS,
) -> ItalyMarket:
    """Assemble one Italian delivery day into a clearable :class:`PowerMarket`.

    The market is built through the same incremental API a user would call
    by hand: zones and interconnectors declared on the ``PowerMarket``
    itself, ``add_bid_curve`` per zone and period, ``set_ntc`` for the
    internal transfer capacities, and a boundary condition per external
    border.

    ``boundary`` selects how the model is closed at the border:

    - ``"prices"`` (default) — each border is a price taker at the
      neighbour's published price, free to trade within the published
      border capacity. This is what pins the *prices* down, and it needs no
      exchange data. It leaves the border *volumes* free, though: inside
      the capacity box many exchange patterns are welfare-equal, so the
      internal flows they imply are not determined.
    - ``"exchanges"`` — each border is fixed at its published exchange
      (``published_exchanges`` required). The borders no longer float, so
      the internal flows become comparable with the published schedule.
      The exchange enters as a problem *input*, the way a TSO's schedule
      would; it is not a tie-break on the Italian flows.
    """

    if boundary not in BOUNDARY_CONDITIONS:
        raise ValueError(
            f"boundary must be one of {sorted(BOUNDARY_CONDITIONS)}, got {boundary!r}"
        )
    if boundary == "exchanges" and published_exchanges is None:
        raise ValueError("the exchanges boundary requires published_exchanges")

    day = date.fromisoformat(str(delivery_day)).isoformat()
    zone_list = tuple(str(zone).upper() for zone in zones)

    curve_rows = rows_for_day(bid_curves, day)
    if curve_rows.empty:
        raise ValueError(f"no bid curve rows found for {day}")
    curves = bid_curves_from_table(curve_rows)
    periods = sorted({period for period, _zone in curves})

    capacities = internal_transfer_capacities(
        transfer_capacities,
        delivery_day=day,
        zones=zone_list,
        periods=periods,
    )
    external_bounds = external_capacity_bounds(
        transfer_capacities,
        delivery_day=day,
        zones=zone_list,
        periods=periods,
    )
    prices = price_mapping(published_prices, delivery_day=day)
    boundary_prices, boundary_diagnostics = external_boundary_prices(
        external_bounds,
        {key: value for key, value in prices.items() if key[1] not in zone_list},
    )

    market = PowerMarket(
        name=f"italy-{day}",
        delivery_day=day,
        zones=list(zone_list),
        interconnectors=sorted(
            {
                (str(row.from_zone), str(row.to_zone))
                for row in capacities.itertuples(index=False)
            }
        ),
        periods=periods,
        metadata={
            "scenario": "italy-replication",
            "boundary_condition": boundary,
        },
    )
    for (period, zone), sides in sorted(curves.items()):
        market.add_bid_curve(zone=zone, period=period, **sides)
    for row in capacities.itertuples(index=False):
        market.set_ntc(
            str(row.from_zone),
            str(row.to_zone),
            period=int(row.period),
            forward_capacity_mwh=float(row.forward_capacity_mwh),
            reverse_capacity_mwh=float(row.reverse_capacity_mwh),
        )
    if boundary == "prices":
        for row in boundary_prices.itertuples(index=False):
            market.add_price_boundary(
                id=str(row.id),
                period=int(row.period),
                zone=str(row.zone),
                external_zone=str(row.external_zone),
                price_eur_per_mwh=float(row.price_eur_per_mwh),
                import_capacity_mwh=float(row.import_capacity_mwh),
                export_capacity_mwh=float(row.export_capacity_mwh),
            )
    else:
        exchanges = rows_for_day(published_exchanges, day)
        for row in exchanges.itertuples(index=False):
            if str(row.zone).upper() not in zone_list:
                continue
            market.add_flow_boundary(
                id=f"exchange_{row.zone}_{row.external_zone}_{int(row.period)}",
                period=int(row.period),
                zone=str(row.zone).upper(),
                external_zone=str(row.external_zone).upper(),
                quantity_mwh=float(row.exchange_mwh),
            )
    market.validate()
    return ItalyMarket(market=market, boundary_diagnostics=boundary_diagnostics)


def replicate_italy_day(
    *,
    delivery_day: str | date,
    bid_curves: pd.DataFrame,
    transfer_capacities: pd.DataFrame,
    published_prices: pd.DataFrame,
    published_flows: pd.DataFrame | None = None,
    published_exchanges: pd.DataFrame | None = None,
    boundary: str = "prices",
    flow_selection: str | None = None,
    zones: Sequence[str] = ITALY_PRICE_AREAS,
    solver: str = "auto",
) -> ItalyReplicationResult:
    """Build, clear, and validate one delivery day against the published results.

    ``flow_selection`` picks the rule that resolves which of the
    welfare-equal flow patterns is returned (see
    :mod:`openeuphemia.solver.flow_selection`); with ``None`` the solver
    returns an arbitrary optimal vertex. The ``"anchored"`` rule matches
    ``published_flows``, so it consumes the outcome it is validated
    against — a reference bound, not a prediction.

    When ``published_flows`` is given the result carries a flow comparison
    alongside the price comparison.
    """

    day = date.fromisoformat(str(delivery_day)).isoformat()
    zone_list = tuple(str(zone).upper() for zone in zones)
    built = build_italy_market(
        delivery_day=day,
        bid_curves=bid_curves,
        transfer_capacities=transfer_capacities,
        published_prices=published_prices,
        published_exchanges=published_exchanges,
        boundary=boundary,
        zones=zone_list,
    )
    market = built.market

    if flow_selection == "anchored" and published_flows is None:
        raise ValueError("the anchored flow selection requires published_flows")
    anchors = (
        flow_mapping(published_flows, market.interconnectors, delivery_day=day)
        if flow_selection == "anchored"
        else None
    )
    clearing = market.clear(
        solver=solver,
        method="per-period-lp",
        flow_selection=flow_selection,
        anchor_flows=anchors,
    )

    prices = price_mapping(published_prices, delivery_day=day)
    price_comparison = compare_prices(
        clearing.prices,
        {key: value for key, value in prices.items() if key[1] in zone_list},
        delivery_day=day,
    )
    flow_comparison = (
        compare_flows(clearing.flows, published_flows, delivery_day=day)
        if published_flows is not None
        else pd.DataFrame()
    )
    dropped = built.boundary_diagnostics[
        built.boundary_diagnostics["treatment"] == "unpriced-dropped"
    ]
    summary = {
        "delivery_day": day,
        "zones": list(zone_list),
        "periods": int(len(market.periods)),
        "orders": int(len(market.orders)),
        "interconnectors": int(len(market.interconnectors)),
        "boundary_price_rows": int(len(market.boundary_prices)),
        "dropped_unpriced_borders": int(len(dropped)),
        "boundary": boundary,
        "flow_selection": flow_selection,
        "objective_value": float(clearing.objective_value),
        **summarize_price_comparison(price_comparison),
        **summarize_flow_comparison(flow_comparison),
    }
    return ItalyReplicationResult(
        delivery_day=day,
        market=market,
        clearing=clearing,
        price_comparison=price_comparison,
        flow_comparison=flow_comparison,
        boundary_diagnostics=built.boundary_diagnostics,
        summary=summary,
    )


def internal_transfer_capacities(
    transfer_capacities: pd.DataFrame,
    *,
    delivery_day: str,
    zones: Sequence[str],
    periods: Sequence[int] | None = None,
) -> pd.DataFrame:
    """Directional capacities between internal zones, ready for ``set_ntc``.

    ``forward_capacity_mwh`` limits flow from ``from_zone`` to ``to_zone``
    and ``reverse_capacity_mwh`` the opposite direction; both are
    non-negative.
    """

    zone_set = {str(zone).upper() for zone in zones}
    frame = _edges_for_day(transfer_capacities, delivery_day, periods)
    frame = frame[
        frame["from_zone"].isin(zone_set) & frame["to_zone"].isin(zone_set)
    ]
    columns = [
        "period",
        "from_zone",
        "to_zone",
        "forward_capacity_mwh",
        "reverse_capacity_mwh",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame(
        {
            "period": frame["period"],
            "from_zone": frame["from_zone"],
            "to_zone": frame["to_zone"],
            "forward_capacity_mwh": frame["max_flow_mwh"],
            "reverse_capacity_mwh": -frame["min_flow_mwh"],
        }
    ).reset_index(drop=True)


def external_capacity_bounds(
    transfer_capacities: pd.DataFrame,
    *,
    delivery_day: str,
    zones: Sequence[str],
    periods: Sequence[int] | None = None,
) -> pd.DataFrame:
    """Orient external-border capacities export-positive from the internal zone.

    Keeps only edges with exactly one internal endpoint and returns one row
    per (edge, period) with non-negative import/export capacities seen from
    the internal zone.
    """

    zone_set = {str(zone).upper() for zone in zones}
    frame = _edges_for_day(transfer_capacities, delivery_day, periods)
    columns = [
        "id",
        "period",
        "zone",
        "external_zone",
        "import_capacity_mwh",
        "export_capacity_mwh",
    ]
    if frame.empty:
        return pd.DataFrame(columns=columns)
    rows: list[dict[str, object]] = []
    for row in frame.itertuples(index=False):
        from_internal = row.from_zone in zone_set
        to_internal = row.to_zone in zone_set
        if from_internal == to_internal:
            continue
        minimum = float(row.min_flow_mwh)
        maximum = float(row.max_flow_mwh)
        if from_internal:
            internal_zone, external_zone = row.from_zone, row.to_zone
            export_capacity, import_capacity = max(maximum, 0.0), max(-minimum, 0.0)
        else:
            internal_zone, external_zone = row.to_zone, row.from_zone
            export_capacity, import_capacity = max(-minimum, 0.0), max(maximum, 0.0)
        rows.append(
            {
                "id": str(row.id),
                "period": int(row.period),
                "zone": internal_zone,
                "external_zone": external_zone,
                "import_capacity_mwh": import_capacity,
                "export_capacity_mwh": export_capacity,
            }
        )
    return pd.DataFrame(rows, columns=columns)


def external_boundary_prices(
    external_bounds: pd.DataFrame,
    boundary_reference_prices: Mapping[tuple[int, str], float],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build price-taking external boundaries from oriented border capacities.

    Each border becomes a fixed-price boundary at the neighbouring zone's
    published price, free to exchange anywhere inside the published border
    capacity. Borders whose neighbouring zone has no published price are
    dropped (reported in the diagnostics as ``unpriced-dropped``).

    Returns ``(boundary_prices, diagnostics)``.
    """

    price_columns = [
        "id",
        "period",
        "zone",
        "external_zone",
        "price_eur_per_mwh",
        "import_capacity_mwh",
        "export_capacity_mwh",
    ]
    price_rows: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    for row in external_bounds.itertuples(index=False):
        period = int(row.period)
        zone = str(row.zone).upper()
        external_zone = str(row.external_zone).upper()
        external_price = boundary_reference_prices.get((period, external_zone))
        priced = external_price is not None and math.isfinite(float(external_price))
        if priced:
            price_rows.append(
                {
                    "id": f"external_price_{row.id}_{period}",
                    "period": period,
                    "zone": zone,
                    "external_zone": external_zone,
                    "price_eur_per_mwh": float(external_price),
                    "import_capacity_mwh": float(row.import_capacity_mwh),
                    "export_capacity_mwh": float(row.export_capacity_mwh),
                }
            )
        diagnostics.append(
            {
                "period": period,
                "id": str(row.id),
                "zone": zone,
                "external_zone": external_zone,
                "external_price_eur_per_mwh": external_price,
                "import_capacity_mwh": float(row.import_capacity_mwh),
                "export_capacity_mwh": float(row.export_capacity_mwh),
                "treatment": "open" if priced else "unpriced-dropped",
            }
        )
    return (
        pd.DataFrame(price_rows, columns=price_columns),
        pd.DataFrame(
            diagnostics,
            columns=[
                "period",
                "id",
                "zone",
                "external_zone",
                "external_price_eur_per_mwh",
                "import_capacity_mwh",
                "export_capacity_mwh",
                "treatment",
            ],
        ),
    )


def price_mapping(
    prices: pd.DataFrame,
    *,
    delivery_day: str | date | None = None,
) -> dict[tuple[int, str], float]:
    """Convert a tidy price table to ``(period, zone) -> price``."""

    frame = prices if delivery_day is None else rows_for_day(prices, delivery_day)
    return {
        (int(row.period), str(row.zone).upper()): float(row.price_eur_per_mwh)
        for row in frame.itertuples(index=False)
        if pd.notna(row.price_eur_per_mwh)
    }


def flow_mapping(
    flows: pd.DataFrame,
    interconnectors: pd.DataFrame,
    *,
    delivery_day: str | date | None = None,
) -> dict[tuple[str, int], float]:
    """Key a tidy flow table by the market's own link ids.

    Flow tables name a link by its endpoints, but a built market names it
    by an id whose orientation may be the opposite one, so each flow is
    re-signed to the direction its interconnector row points in.
    """

    published = _canonical_flows(flows, delivery_day)
    anchors: dict[tuple[str, int], float] = {}
    for row in interconnectors.itertuples(index=False):
        pair, sign = _canonical_pair(row.from_zone, row.to_zone)
        value = published.get((int(row.period), pair))
        if value is not None:
            anchors[(str(row.id), int(row.period))] = sign * value
    return anchors


def rows_for_day(frame: pd.DataFrame, delivery_day: str | date) -> pd.DataFrame:
    """Select the rows of a multi-day table belonging to one delivery day."""

    day = date.fromisoformat(str(delivery_day)).isoformat()
    if frame.empty or "delivery_day" not in frame.columns:
        return frame.copy()
    return frame[frame["delivery_day"].astype(str) == day].copy()


def delivery_days(frame: pd.DataFrame) -> list[str]:
    """List the delivery days present in a multi-day table."""

    if frame.empty or "delivery_day" not in frame.columns:
        return []
    return sorted(frame["delivery_day"].astype(str).unique())


def compare_prices(
    prices: pd.DataFrame,
    reference_prices: Mapping[tuple[int, str], float],
    *,
    delivery_day: str,
) -> pd.DataFrame:
    """One row per published (period, zone) price with the model's error."""

    estimates = {
        (int(row.period), str(row.zone).upper()): float(row.price_eur_per_mwh)
        for row in prices.itertuples(index=False)
    }
    rows: list[dict[str, object]] = []
    for key, reference in sorted(reference_prices.items()):
        period, zone = int(key[0]), str(key[1]).upper()
        estimated = estimates.get((period, zone))
        error = math.nan if estimated is None else float(estimated) - float(reference)
        rows.append(
            {
                "delivery_day": delivery_day,
                "period": period,
                "zone": zone,
                "modelled_price_eur_per_mwh": estimated,
                "published_price_eur_per_mwh": float(reference),
                "error_eur_per_mwh": error,
                "absolute_error_eur_per_mwh": abs(error)
                if math.isfinite(error)
                else math.nan,
            }
        )
    return pd.DataFrame(rows)


def compare_flows(
    flows: pd.DataFrame,
    published_flows: pd.DataFrame,
    *,
    delivery_day: str,
) -> pd.DataFrame:
    """One row per published (link, period) flow with the model's error.

    Both sides are compared in a canonical orientation, so it does not
    matter which way round either table names a link.
    """

    modelled = flows[flows["flow_type"] == "interconnector"] if not flows.empty else flows
    estimates = _canonical_flows(modelled, None)
    rows: list[dict[str, object]] = []
    for key, reference in sorted(_canonical_flows(published_flows, delivery_day).items()):
        period, (from_zone, to_zone) = int(key[0]), key[1]
        estimated = estimates.get(key)
        error = math.nan if estimated is None else float(estimated) - float(reference)
        rows.append(
            {
                "delivery_day": delivery_day,
                "period": period,
                "from_zone": from_zone,
                "to_zone": to_zone,
                "modelled_flow_mwh": estimated,
                "published_flow_mwh": float(reference),
                "error_mwh": error,
                "absolute_error_mwh": abs(error) if math.isfinite(error) else math.nan,
            }
        )
    return pd.DataFrame(rows)


def _canonical_pair(from_zone: Any, to_zone: Any) -> tuple[tuple[str, str], float]:
    """Order a link's endpoints, with the sign that re-orients its flow."""

    a, b = str(from_zone).upper(), str(to_zone).upper()
    return ((a, b), 1.0) if a <= b else ((b, a), -1.0)


def _canonical_flows(
    flows: pd.DataFrame,
    delivery_day: str | date | None,
) -> dict[tuple[int, tuple[str, str]], float]:
    frame = flows if delivery_day is None else rows_for_day(flows, delivery_day)
    result: dict[tuple[int, tuple[str, str]], float] = {}
    for row in frame.itertuples(index=False):
        if pd.isna(row.flow_mwh):
            continue
        pair, sign = _canonical_pair(row.from_zone, row.to_zone)
        result[(int(row.period), pair)] = sign * float(row.flow_mwh)
    return result


def summarize_flow_comparison(
    comparison: pd.DataFrame,
    *,
    tolerance_mwh: float = FLOW_TOLERANCE_MWH,
) -> dict[str, Any]:
    """MAE, max error, and exact-row count of a flow comparison frame."""

    if comparison.empty:
        return {
            "flow_rows": 0,
            "exact_flow_rows": 0,
            "flow_mae_mwh": None,
            "flow_max_abs_error_mwh": None,
        }
    errors = comparison["absolute_error_mwh"].dropna()
    return {
        "flow_rows": int(len(errors)),
        "exact_flow_rows": int((errors <= tolerance_mwh).sum()),
        "flow_mae_mwh": float(errors.mean()) if len(errors) else None,
        "flow_max_abs_error_mwh": float(errors.max()) if len(errors) else None,
    }


def summarize_price_comparison(
    comparison: pd.DataFrame,
    *,
    tolerance_eur: float = PRICE_TOLERANCE_EUR,
) -> dict[str, Any]:
    """MAE, max error, and exact-row count of a price comparison frame."""

    if comparison.empty:
        return {
            "price_rows": 0,
            "matched_rows": 0,
            "exact_rows": 0,
            "price_mae_eur_per_mwh": None,
            "price_max_abs_error_eur_per_mwh": None,
        }
    errors = comparison["absolute_error_eur_per_mwh"].dropna()
    return {
        "price_rows": int(len(comparison)),
        "matched_rows": int(len(errors)),
        "exact_rows": int((errors <= tolerance_eur).sum()),
        "price_mae_eur_per_mwh": float(errors.mean()) if len(errors) else None,
        "price_max_abs_error_eur_per_mwh": float(errors.max()) if len(errors) else None,
    }


def _edges_for_day(
    transfer_capacities: pd.DataFrame,
    delivery_day: str | date,
    periods: Sequence[int] | None,
) -> pd.DataFrame:
    frame = rows_for_day(transfer_capacities, delivery_day)
    if frame.empty:
        return frame
    required = {"period", "from_zone", "to_zone", "min_flow_mwh", "max_flow_mwh"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"transfer capacity table is missing columns: {missing}")
    frame = frame.copy()
    frame["period"] = frame["period"].astype(int)
    frame["from_zone"] = frame["from_zone"].astype(str).str.upper()
    frame["to_zone"] = frame["to_zone"].astype(str).str.upper()
    frame["min_flow_mwh"] = frame["min_flow_mwh"].astype(float)
    frame["max_flow_mwh"] = frame["max_flow_mwh"].astype(float)
    if "id" not in frame.columns:
        frame["id"] = frame["from_zone"] + "_" + frame["to_zone"]
    if periods is not None:
        frame = frame[frame["period"].isin({int(period) for period in periods})]
    return frame
