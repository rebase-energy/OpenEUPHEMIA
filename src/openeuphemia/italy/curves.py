"""Aggregated zonal bid/offer curves from the GME MGP public order book.

The published order book contains every submitted bid and offer with its
zone, price, quantity, and acceptance status. Summing the simple (hourly)
offers per (period, zone, side, price) yields the aggregated zonal curves
that the clearing model consumes.

Block orders (``offer_type`` "B", introduced to the MGP in 2025) are
all-or-nothing across multiple periods and cannot be represented inside a
convex zonal curve — an accepted block may sit above the zonal price and a
paradoxically rejected block below it. Following the standard EUPHEMIA
price decomposition, the published block decisions are fixed: accepted
blocks enter the curves as price-taking volumes at their awarded
quantities, rejected blocks are dropped, and prices come from clearing the
remaining convex market.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from openeuphemia.gme.offers import PUBLIC_CURVE_STATUS_CODES, SIDE_BY_PURPOSE

ITALY_PRICE_AREAS = ("NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD")

GME_PRICE_FLOOR_EUR = 0.0
GME_PRICE_CAP_EUR = 4000.0
BLOCK_FIXING_SUPPLY_PRICE_EUR = -500.0
BLOCK_FIXING_DEMAND_PRICE_EUR = 4000.0

TOLERANCE = 1e-9

CURVE_COLUMNS = (
    "delivery_day",
    "period",
    "zone",
    "side",
    "price_eur_per_mwh",
    "quantity_mwh",
)


def zonal_curves_from_offers(
    offers: pd.DataFrame,
    *,
    delivery_day: str,
    zones: Sequence[str] = ITALY_PRICE_AREAS,
) -> tuple[pd.DataFrame, int]:
    """Build aggregated zonal curves from the processed public order book.

    Returns ``(curves, block_fixing_rows)`` where ``curves`` follows
    ``CURVE_COLUMNS`` and ``block_fixing_rows`` counts the price-taking rows
    added for accepted block orders.
    """

    zone_list = tuple(str(zone).upper() for zone in zones)
    remaining, block_fixings = split_block_offer_fixings(
        offers,
        delivery_day=delivery_day,
        zones=zone_list,
    )
    data = filtered_public_offer_rows(
        remaining,
        delivery_day=delivery_day,
        zones=zone_list,
    )
    curves = _zonal_curves_from_public_offers(data, delivery_day=delivery_day)
    if not block_fixings.empty:
        curves = pd.concat([curves, block_fixings], ignore_index=True)
    return curves, int(len(block_fixings))


def filtered_public_offer_rows(
    offers: pd.DataFrame,
    *,
    delivery_day: str,
    zones: Sequence[str],
) -> pd.DataFrame:
    """Keep the priced ACC/REJ simple-offer rows for the requested day/zones."""

    required = {
        "delivery_date",
        "interval_no",
        "zone_cd",
        "purpose_cd",
        "status_cd",
        "energy_price_eur_per_mwh",
        "adj_quantity_mw",
    }
    missing = sorted(required - set(offers.columns))
    if missing:
        raise ValueError(f"processed GME frame is missing columns: {missing}")

    zone_set = {str(zone).upper() for zone in zones}
    data = offers.copy()
    data["delivery_date"] = data["delivery_date"].astype(str)
    data["zone_cd"] = data["zone_cd"].astype(str).str.upper()
    data["side"] = data["purpose_cd"].map(SIDE_BY_PURPOSE)
    data = data[
        (data["delivery_date"] == delivery_day)
        & data["zone_cd"].isin(zone_set)
        & data["side"].notna()
        & data["status_cd"].isin(PUBLIC_CURVE_STATUS_CODES)
        & data["adj_quantity_mw"].notna()
    ].copy()
    data["interval_no"] = data["interval_no"].astype(int)
    for column in ("energy_price_eur_per_mwh", "adj_quantity_mw"):
        data[column] = pd.to_numeric(data[column], errors="coerce")
    return data


def split_block_offer_fixings(
    offers: pd.DataFrame,
    *,
    delivery_day: str,
    zones: Sequence[str],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fix observed block-order decisions and return the simple offers.

    Returns ``(simple_offers, block_fixing_curve_rows)``. Accepted blocks
    become price-taking curve rows (supply at ``BLOCK_FIXING_SUPPLY_PRICE_EUR``
    below the floor, demand at the cap) so the LP always accepts them at the
    awarded quantity; rejected blocks are dropped.
    """

    columns = list(CURVE_COLUMNS)
    if "offer_type" not in offers.columns:
        return offers, pd.DataFrame(columns=columns)
    is_block = offers["offer_type"].astype(str).str.upper() == "B"
    if not is_block.any():
        return offers, pd.DataFrame(columns=columns)
    blocks = offers[is_block].copy()
    remaining = offers[~is_block].copy()
    zone_set = {str(zone).upper() for zone in zones}
    blocks["side"] = blocks["purpose_cd"].map(SIDE_BY_PURPOSE)
    accepted = blocks[
        (blocks["delivery_date"].astype(str) == str(delivery_day))
        & (blocks["status_cd"].astype(str) == "ACC")
        & blocks["zone_cd"].astype(str).str.upper().isin(zone_set)
        & blocks["side"].notna()
        & (pd.to_numeric(blocks["awarded_quantity_mw"], errors="coerce") > 0)
    ].copy()
    if accepted.empty:
        return remaining, pd.DataFrame(columns=columns)
    accepted["quantity_mwh"] = pd.to_numeric(accepted["awarded_quantity_mw"])
    grouped = (
        accepted.groupby(
            [
                accepted["interval_no"].astype(int),
                accepted["zone_cd"].astype(str).str.upper(),
                accepted["side"],
            ]
        )["quantity_mwh"]
        .sum()
        .reset_index()
    )
    grouped.columns = ["period", "zone", "side", "quantity_mwh"]
    grouped["delivery_day"] = str(delivery_day)
    grouped["price_eur_per_mwh"] = [
        BLOCK_FIXING_DEMAND_PRICE_EUR
        if side == "demand"
        else BLOCK_FIXING_SUPPLY_PRICE_EUR
        for side in grouped["side"]
    ]
    return remaining, grouped[columns]


def orders_from_zonal_curves(curves: pd.DataFrame) -> pd.DataFrame:
    """Turn aggregated zonal curve rows into solver order rows."""

    if curves.empty:
        return pd.DataFrame(
            columns=["id", "period", "zone", "side", "price_eur_per_mwh", "quantity_mwh"]
        )
    frame = curves.copy()
    required = {"period", "zone", "side", "price_eur_per_mwh", "quantity_mwh"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"zonal curves are missing columns: {sorted(missing)}")
    frame = frame[frame["quantity_mwh"].astype(float).abs() > TOLERANCE].copy()
    frame["zone"] = frame["zone"].astype(str).str.upper()
    frame["period"] = frame["period"].astype(int)
    frame = frame.sort_values(
        ["period", "zone", "side", "price_eur_per_mwh"],
        kind="stable",
    ).reset_index(drop=True)
    frame["id"] = [
        f"{row.zone}_{row.side}_{int(row.period)}_{index}"
        for index, row in enumerate(frame.itertuples(index=False))
    ]
    return frame[
        ["id", "period", "zone", "side", "price_eur_per_mwh", "quantity_mwh"]
    ].reset_index(drop=True)


def _zonal_curves_from_public_offers(
    data: pd.DataFrame,
    *,
    delivery_day: str,
) -> pd.DataFrame:
    grouped = (
        data.groupby(
            ["interval_no", "zone_cd", "side", "energy_price_eur_per_mwh"],
            dropna=False,
        )
        .agg(quantity_mwh=("adj_quantity_mw", "sum"))
        .reset_index()
    )
    grouped = grouped[grouped["quantity_mwh"].abs() > TOLERANCE].copy()
    grouped["delivery_day"] = delivery_day
    grouped = grouped.rename(
        columns={
            "interval_no": "period",
            "zone_cd": "zone",
            "energy_price_eur_per_mwh": "price_eur_per_mwh",
        }
    )
    return (
        grouped[list(CURVE_COLUMNS)]
        .sort_values(list(CURVE_COLUMNS[:-1]), kind="stable")
        .reset_index(drop=True)
    )
