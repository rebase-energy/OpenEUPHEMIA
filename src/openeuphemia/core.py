"""Public SDK object model for component-table market clearing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping

import pandas as pd

if TYPE_CHECKING:
    from openeuphemia.curves import BidCurve
    from openeuphemia.system import System

COUPLING_MODES = ("ntc",)

DEMAND = "demand"
SUPPLY = "supply"
SIDES = {DEMAND, SUPPLY}

ORDER_COLUMNS = (
    "id",
    "period",
    "zone",
    "side",
    "quantity_mwh",
    "price_eur_per_mwh",
)
BLOCK_ORDER_COLUMNS = (
    "id",
    "block_id",
    "period",
    "zone",
    "side",
    "quantity_mwh",
    "price_eur_per_mwh",
)
INTERCONNECTOR_COLUMNS = (
    "id",
    "period",
    "from_zone",
    "to_zone",
    "min_flow_mwh",
    "max_flow_mwh",
)
BOUNDARY_FLOW_COLUMNS = ("id", "period", "zone", "quantity_mwh")
BOUNDARY_PRICE_COLUMNS = (
    "id",
    "period",
    "zone",
    "price_eur_per_mwh",
    "import_capacity_mwh",
    "export_capacity_mwh",
)


@dataclass(frozen=True)
class ClearingOptions:
    """Options for clearing a component-table market."""

    solver: str = "auto"
    method: str = "full-milp"
    iterations_count: int = 150


@dataclass(frozen=True)
class MarketClearingResult:
    """Table-shaped clearing result returned by the SDK API."""

    delivery_day: str
    status: str
    objective_value: float
    solver: str
    prices: pd.DataFrame
    flows: pd.DataFrame = field(default_factory=pd.DataFrame)
    accepted_orders: pd.DataFrame = field(default_factory=pd.DataFrame)
    iterations: tuple[dict[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def accepted_bids(self) -> pd.DataFrame:
        """Alias for ``accepted_orders``."""

        return self.accepted_orders

    @property
    def price_table(self) -> pd.DataFrame:
        """Zonal prices pivoted to a period-by-zone table."""

        if self.prices.empty:
            return pd.DataFrame()
        return self.prices.pivot(
            index="period", columns="zone", values="price_eur_per_mwh"
        )

    def to_dataframes(self) -> dict[str, pd.DataFrame]:
        return {
            "prices": self.prices.copy(),
            "flows": self.flows.copy(),
            "accepted_orders": self.accepted_orders.copy(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "delivery_day": self.delivery_day,
            "status": self.status,
            "objective_value": self.objective_value,
            "solver": self.solver,
            "prices": _records(self.prices),
            "flows": _records(self.flows),
            "accepted_orders": _records(self.accepted_orders),
            "iterations": list(self.iterations),
            "metadata": _public_metadata(self.metadata),
        }


class Market:
    """Day-ahead zonal market represented by PyPSA-like component tables."""

    def __init__(
        self,
        *,
        name: str = "market",
        delivery_day: str,
        system: System | None = None,
        coupling: str | None = None,
        zones: pd.DataFrame | list[str] | tuple[str, ...] | None = None,
        periods: pd.DataFrame | list[int] | tuple[int, ...] | None = None,
        orders: pd.DataFrame | None = None,
        block_orders: pd.DataFrame | None = None,
        complex_orders: pd.DataFrame | None = None,
        interconnectors: pd.DataFrame | None = None,
        boundary_flows: pd.DataFrame | None = None,
        boundary_prices: pd.DataFrame | None = None,
        flow_based_constraints: pd.DataFrame | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if not delivery_day:
            raise ValueError("delivery_day must be non-empty")
        normalized_coupling = (coupling or "ntc").lower()
        if normalized_coupling not in COUPLING_MODES:
            raise ValueError(
                f"coupling must be one of {sorted(COUPLING_MODES)}, got {coupling!r}"
            )
        if system is not None and zones is not None:
            raise ValueError("pass either system or zones, not both")
        self.name = name
        self.delivery_day = str(delivery_day)
        self.system = system
        self.coupling = normalized_coupling
        self._ntc: dict[tuple[str, str], dict[int | None, tuple[float, float]]] = {}
        if system is not None:
            zones = list(system.zones)
        self.zones = _zones_frame(zones)
        self.periods = _periods_frame(periods)
        self.orders = _copy_or_empty(orders)
        self.block_orders = _copy_or_empty(block_orders)
        self.complex_orders = _copy_or_empty(complex_orders)
        self.interconnectors = _copy_or_empty(interconnectors)
        self.boundary_flows = _copy_or_empty(boundary_flows)
        self.boundary_prices = _copy_or_empty(boundary_prices)
        self.flow_based_constraints = _copy_or_empty(flow_based_constraints)
        self.metadata: dict[str, Any] = dict(metadata or {})

    @classmethod
    def from_dataframes(
        cls,
        *,
        name: str = "market",
        delivery_day: str,
        zones: pd.DataFrame | list[str] | tuple[str, ...] | None = None,
        periods: pd.DataFrame | list[int] | tuple[int, ...] | None = None,
        orders: pd.DataFrame | None = None,
        block_orders: pd.DataFrame | None = None,
        complex_orders: pd.DataFrame | None = None,
        interconnectors: pd.DataFrame | None = None,
        boundary_flows: pd.DataFrame | None = None,
        boundary_prices: pd.DataFrame | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> Market:
        return cls(
            name=name,
            delivery_day=delivery_day,
            zones=zones,
            periods=periods,
            orders=orders,
            block_orders=block_orders,
            complex_orders=complex_orders,
            interconnectors=interconnectors,
            boundary_flows=boundary_flows,
            boundary_prices=boundary_prices,
            metadata=metadata,
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> Market:
        return cls(
            name=str(value.get("name", "market")),
            delivery_day=str(value["delivery_day"]),
            zones=pd.DataFrame(value.get("zones", ())),
            periods=pd.DataFrame(value.get("periods", ())),
            orders=pd.DataFrame(value.get("orders", ())),
            block_orders=pd.DataFrame(value.get("block_orders", ())),
            complex_orders=pd.DataFrame(value.get("complex_orders", ())),
            interconnectors=pd.DataFrame(value.get("interconnectors", ())),
            boundary_flows=pd.DataFrame(value.get("boundary_flows", ())),
            boundary_prices=pd.DataFrame(value.get("boundary_prices", ())),
            metadata=dict(value.get("metadata", {})),
        )

    def add_zones(self, zones: list[str] | tuple[str, ...]) -> None:
        self.zones = _zones_frame(zones)

    def add_periods(self, periods: list[int] | tuple[int, ...]) -> None:
        self.periods = _periods_frame(periods)

    def add_bid_curve(
        self,
        *,
        zone: str,
        period: int,
        supply: BidCurve | None = None,
        demand: BidCurve | None = None,
        name: str | None = None,
    ) -> None:
        """Add aggregated supply and/or demand bid curves for one zone and period.

        Curves are lowered to simple orders in the ``orders`` component table.
        Order ids derive from ``name`` (default ``{zone}_p{period}_{side}``)
        and are suffixed automatically if a curve with the same name was
        already added.
        """

        if supply is None and demand is None:
            raise ValueError("add_bid_curve requires a supply and/or demand curve")
        self._require_system_zone(zone)
        existing_ids: set[str] = (
            set()
            if self.orders.empty or "id" not in self.orders.columns
            else set(self.orders["id"].astype(str))
        )
        rows: list[dict[str, Any]] = []
        for side, curve in ((SUPPLY, supply), (DEMAND, demand)):
            if curve is None:
                continue
            base_prefix = f"{name}_{side}" if name else f"{zone}_p{period}_{side}"
            prefix = base_prefix
            attempt = 2
            while any(row["id"] in existing_ids for row in curve.to_orders(
                zone=zone, period=period, side=side, id_prefix=prefix
            )):
                prefix = f"{base_prefix}_{attempt}"
                attempt += 1
            side_rows = curve.to_orders(
                zone=zone, period=period, side=side, id_prefix=prefix
            )
            existing_ids.update(row["id"] for row in side_rows)
            rows.extend(side_rows)
        for row in rows:
            self.orders = _append_row(self.orders, row)

    def add_block_order(
        self,
        *,
        id: str,
        zone: str,
        side: str,
        periods: int | Sequence[int],
        quantity_mwh: float,
        price_eur_per_mwh: float,
    ) -> None:
        """Add an all-or-nothing block order spanning one or more periods."""

        if side not in SIDES:
            raise ValueError(f"side must be one of {sorted(SIDES)}, got {side!r}")
        self._require_system_zone(zone)
        period_list = [periods] if isinstance(periods, int) else list(periods)
        if not period_list:
            raise ValueError("block order requires at least one period")
        for period in period_list:
            self.block_orders = _append_row(
                self.block_orders,
                {
                    "id": f"{id}_p{int(period)}",
                    "block_id": id,
                    "period": int(period),
                    "zone": zone,
                    "side": side,
                    "quantity_mwh": quantity_mwh,
                    "price_eur_per_mwh": price_eur_per_mwh,
                },
            )

    def set_ntc(
        self,
        from_zone: str,
        to_zone: str,
        *,
        capacity_mwh: float | None = None,
        forward_capacity_mwh: float | None = None,
        reverse_capacity_mwh: float | None = None,
        period: int | None = None,
    ) -> None:
        """Set the net transfer capacity of a system interconnector.

        ``capacity_mwh`` applies symmetrically in both directions;
        ``forward_capacity_mwh`` limits flow from ``from_zone`` to
        ``to_zone`` and ``reverse_capacity_mwh`` the opposite direction.
        With ``period=None`` the value applies to every period without an
        explicit per-period override. Interconnectors without any NTC are
        unconstrained.
        """

        if self.system is None:
            raise ValueError("set_ntc requires a market built from a System")
        if not self.system.has_interconnector(from_zone, to_zone):
            raise ValueError(
                f"system has no interconnector between {from_zone!r} and {to_zone!r}"
            )
        if capacity_mwh is not None:
            if forward_capacity_mwh is not None or reverse_capacity_mwh is not None:
                raise ValueError(
                    "pass either capacity_mwh or directional capacities, not both"
                )
            forward_capacity_mwh = reverse_capacity_mwh = capacity_mwh
        if forward_capacity_mwh is None and reverse_capacity_mwh is None:
            raise ValueError("set_ntc requires a capacity")

        pair = (from_zone, to_zone)
        if pair not in self.system.interconnectors:
            pair = (to_zone, from_zone)
            forward_capacity_mwh, reverse_capacity_mwh = (
                reverse_capacity_mwh,
                forward_capacity_mwh,
            )
        limits = self._ntc.setdefault(pair, {})
        previous_min, previous_max = limits.get(
            period, (-float("inf"), float("inf"))
        )
        max_flow = forward_capacity_mwh if forward_capacity_mwh is not None else previous_max
        min_flow = -reverse_capacity_mwh if reverse_capacity_mwh is not None else previous_min
        for name, value in (("forward", max_flow), ("reverse", -min_flow)):
            if value < 0:
                raise ValueError(f"{name} capacity must be non-negative")
        limits[period] = (min_flow, max_flow)

    def _require_system_zone(self, zone: str) -> None:
        if self.system is not None and zone not in self.system.zones:
            raise ValueError(f"unknown zone {zone!r}; not part of the market's System")

    def _materialize_system_interconnectors(self, periods: Sequence[int]) -> None:
        if self.system is None:
            return
        existing: set[tuple[str, int]] = set()
        if not self.interconnectors.empty and {
            "id",
            "period",
        } <= set(self.interconnectors.columns):
            existing = {
                (str(row["id"]), int(row["period"]))
                for _, row in self.interconnectors.iterrows()
            }
        for from_zone, to_zone in self.system.interconnectors:
            limits = self._ntc.get((from_zone, to_zone), {})
            link_id = f"{from_zone}-{to_zone}"
            for period in periods:
                if (link_id, int(period)) in existing:
                    continue
                min_flow, max_flow = limits.get(
                    int(period), limits.get(None, (-float("inf"), float("inf")))
                )
                self.interconnectors = _append_row(
                    self.interconnectors,
                    {
                        "id": link_id,
                        "period": int(period),
                        "from_zone": from_zone,
                        "to_zone": to_zone,
                        "min_flow_mwh": min_flow,
                        "max_flow_mwh": max_flow,
                    },
                )

    def add_fixed_flow_boundary(
        self,
        *,
        id: str,
        period: int,
        zone: str,
        quantity_mwh: float,
        external_zone: str | None = None,
    ) -> None:
        """Add an exogenous boundary exchange.

        Positive ``quantity_mwh`` means net export from ``zone`` to the external
        system. Negative values mean net import into ``zone``.
        """

        self.boundary_flows = _append_row(
            self.boundary_flows,
            {
                "id": id,
                "period": period,
                "zone": zone,
                "quantity_mwh": quantity_mwh,
                "external_zone": external_zone,
            },
        )

    def add_fixed_price_boundary(
        self,
        *,
        id: str,
        period: int,
        zone: str,
        price_eur_per_mwh: float,
        import_capacity_mwh: float,
        export_capacity_mwh: float,
        external_zone: str | None = None,
    ) -> None:
        """Add a fixed-price external market proxy.

        The solver chooses a signed boundary exchange. Positive values are
        exports from ``zone`` at ``price_eur_per_mwh``; negative values are
        imports into ``zone`` at that price.
        """

        self.boundary_prices = _append_row(
            self.boundary_prices,
            {
                "id": id,
                "period": period,
                "zone": zone,
                "price_eur_per_mwh": price_eur_per_mwh,
                "import_capacity_mwh": import_capacity_mwh,
                "export_capacity_mwh": export_capacity_mwh,
                "external_zone": external_zone,
            },
        )

    def clear(
        self,
        *,
        solver: str = "auto",
        method: str = "full-milp",
        options: ClearingOptions | None = None,
        iterations_count: int | None = None,
    ) -> MarketClearingResult:
        """Clear the market.

        ``solver`` selects the optimization backend (``"auto"`` prefers
        HiGHS). ``method`` selects the clearing formulation: ``"full-milp"``
        solves one joint welfare-maximization MILP across all periods,
        ``"per-period-lp"`` clears each period as an independent LP — faster
        for large curve-only markets, but without per-order acceptance.
        """

        from openeuphemia.solver import clear_market

        clearing_options = options or ClearingOptions(
            solver=solver,
            method=method,
            iterations_count=iterations_count or 150,
        )
        return clear_market(
            self,
            solver=clearing_options.solver,
            method=clearing_options.method,
            iterations_count=clearing_options.iterations_count,
        )

    def validate(self) -> None:
        self.orders = _orders_frame(self.orders)
        self.block_orders = _block_orders_frame(self.block_orders)
        self.interconnectors = _interconnectors_frame(self.interconnectors)
        self.boundary_flows = _boundary_flows_frame(self.boundary_flows)
        self.boundary_prices = _boundary_prices_frame(self.boundary_prices)
        self.zones = _zones_frame(self.zones)
        self.periods = _periods_frame(self.periods)

        inferred_zones = _infer_zones(
            self.orders,
            self.block_orders,
            self.interconnectors,
            self.boundary_flows,
            self.boundary_prices,
        )
        if self.zones.empty and inferred_zones:
            self.zones = _zones_frame(sorted(inferred_zones))
        zone_set = set(self.zones["zone"])

        inferred_periods = _infer_periods(
            self.orders,
            self.block_orders,
            self.interconnectors,
            self.boundary_flows,
            self.boundary_prices,
        )
        if self.periods.empty and inferred_periods:
            self.periods = _periods_frame(sorted(inferred_periods))
        period_set = set(self.periods["period"])

        if not zone_set:
            raise ValueError("market requires at least one zone")
        if not period_set:
            raise ValueError("market requires at least one period")

        self._materialize_system_interconnectors(sorted(period_set))
        self.interconnectors = _interconnectors_frame(self.interconnectors)

        _validate_ids(self.orders, "orders", "id")
        _validate_ids(self.block_orders, "block_orders", "id")
        _validate_ids(self.interconnectors, "interconnectors", "id", allow_repeated=True)
        _validate_ids(self.boundary_flows, "boundary_flows", "id", allow_repeated=True)
        _validate_ids(self.boundary_prices, "boundary_prices", "id", allow_repeated=True)
        if (
            not self.boundary_prices.empty
            and self.boundary_prices[["id", "period"]].duplicated().any()
        ):
            raise ValueError("boundary_prices id/period pairs must be unique")

        for table_name, table in (
            ("orders", self.orders),
            ("block_orders", self.block_orders),
            ("boundary_flows", self.boundary_flows),
            ("boundary_prices", self.boundary_prices),
        ):
            if table.empty:
                continue
            unknown_zones = set(table["zone"]) - zone_set
            if unknown_zones:
                raise ValueError(f"{table_name} references unknown zones {sorted(unknown_zones)}")
            unknown_periods = set(table["period"]) - period_set
            if unknown_periods:
                raise ValueError(
                    f"{table_name} references unknown periods {sorted(unknown_periods)}"
                )

        if not self.interconnectors.empty:
            unknown_zones = (
                set(self.interconnectors["from_zone"])
                | set(self.interconnectors["to_zone"])
            ) - zone_set
            if unknown_zones:
                raise ValueError(
                    f"interconnectors reference unknown zones {sorted(unknown_zones)}"
                )
            unknown_periods = set(self.interconnectors["period"]) - period_set
            if unknown_periods:
                raise ValueError(
                    f"interconnectors reference unknown periods {sorted(unknown_periods)}"
                )
            invalid_limits = self.interconnectors[
                self.interconnectors["min_flow_mwh"]
                > self.interconnectors["max_flow_mwh"]
            ]
            if not invalid_limits.empty:
                raise ValueError("interconnector min_flow_mwh exceeds max_flow_mwh")

    def to_dataframes(self) -> dict[str, pd.DataFrame]:
        return {
            "zones": self.zones.copy(),
            "periods": self.periods.copy(),
            "orders": self.orders.copy(),
            "block_orders": self.block_orders.copy(),
            "complex_orders": self.complex_orders.copy(),
            "interconnectors": self.interconnectors.copy(),
            "boundary_flows": self.boundary_flows.copy(),
            "boundary_prices": self.boundary_prices.copy(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "delivery_day": self.delivery_day,
            "zones": _records(self.zones),
            "periods": _records(self.periods),
            "orders": _records(self.orders),
            "block_orders": _records(self.block_orders),
            "complex_orders": _records(self.complex_orders),
            "interconnectors": _records(self.interconnectors),
            "boundary_flows": _records(self.boundary_flows),
            "boundary_prices": _records(self.boundary_prices),
            "metadata": _public_metadata(self.metadata),
        }


def _copy_or_empty(value: pd.DataFrame | None) -> pd.DataFrame:
    return value.copy() if value is not None else pd.DataFrame()


def _append_row(frame: pd.DataFrame, row: Mapping[str, Any]) -> pd.DataFrame:
    row_frame = pd.DataFrame([row])
    if frame.empty:
        return row_frame
    return pd.concat([frame, row_frame], ignore_index=True)


def _zones_frame(value: pd.DataFrame | list[str] | tuple[str, ...] | None) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame(columns=["zone"])
    if isinstance(value, pd.DataFrame):
        frame = _ensure_named_index(value.copy(), "zone")
    else:
        frame = pd.DataFrame({"zone": list(value)})
    if frame.empty:
        return pd.DataFrame(columns=["zone"])
    _require_columns(frame, ("zone",), "zones")
    frame = frame[["zone"] + [col for col in frame.columns if col != "zone"]].copy()
    frame["zone"] = frame["zone"].astype(str)
    if frame["zone"].duplicated().any():
        raise ValueError("zones must be unique")
    return frame.reset_index(drop=True)


def _periods_frame(value: pd.DataFrame | list[int] | tuple[int, ...] | None) -> pd.DataFrame:
    if value is None:
        return pd.DataFrame(columns=["period"])
    if isinstance(value, pd.DataFrame):
        frame = _ensure_named_index(value.copy(), "period")
    else:
        frame = pd.DataFrame({"period": list(value)})
    if frame.empty:
        return pd.DataFrame(columns=["period"])
    _require_columns(frame, ("period",), "periods")
    frame = frame[["period"] + [col for col in frame.columns if col != "period"]].copy()
    frame["period"] = frame["period"].astype(int)
    if (frame["period"] <= 0).any():
        raise ValueError("periods must be positive integers")
    if frame["period"].duplicated().any():
        raise ValueError("periods must be unique")
    return frame.reset_index(drop=True)


def _orders_frame(value: pd.DataFrame) -> pd.DataFrame:
    frame = _quantity_alias(value.copy())
    if frame.empty:
        return pd.DataFrame(columns=list(ORDER_COLUMNS))
    _require_columns(frame, ORDER_COLUMNS, "orders")
    _normalize_order_common(frame)
    return frame.reset_index(drop=True)


def _block_orders_frame(value: pd.DataFrame) -> pd.DataFrame:
    frame = _quantity_alias(value.copy())
    if frame.empty:
        return pd.DataFrame(columns=[*BLOCK_ORDER_COLUMNS, "parent_block_id"])
    _require_columns(frame, BLOCK_ORDER_COLUMNS, "block_orders")
    _normalize_order_common(frame)
    frame["block_id"] = frame["block_id"].astype(str)
    if "parent_block_id" not in frame.columns:
        frame["parent_block_id"] = pd.NA
    return frame.reset_index(drop=True)


def _interconnectors_frame(value: pd.DataFrame) -> pd.DataFrame:
    frame = value.copy()
    if frame.empty:
        return pd.DataFrame(columns=list(INTERCONNECTOR_COLUMNS))
    if "min_flow_mwh" not in frame.columns and "capacity_mwh" in frame.columns:
        frame["min_flow_mwh"] = -frame["capacity_mwh"].astype(float)
    if "max_flow_mwh" not in frame.columns and "capacity_mwh" in frame.columns:
        frame["max_flow_mwh"] = frame["capacity_mwh"].astype(float)
    _require_columns(frame, INTERCONNECTOR_COLUMNS, "interconnectors")
    for column in ("id", "from_zone", "to_zone"):
        frame[column] = frame[column].astype(str)
    frame["period"] = frame["period"].astype(int)
    for column in ("min_flow_mwh", "max_flow_mwh"):
        frame[column] = frame[column].astype(float)
    return frame.reset_index(drop=True)


def _boundary_flows_frame(value: pd.DataFrame) -> pd.DataFrame:
    frame = _quantity_alias(value.copy())
    if frame.empty:
        return pd.DataFrame(columns=[*BOUNDARY_FLOW_COLUMNS, "external_zone"])
    _require_columns(frame, BOUNDARY_FLOW_COLUMNS, "boundary_flows")
    frame["id"] = frame["id"].astype(str)
    frame["period"] = frame["period"].astype(int)
    frame["zone"] = frame["zone"].astype(str)
    frame["quantity_mwh"] = frame["quantity_mwh"].astype(float)
    if "external_zone" not in frame.columns:
        frame["external_zone"] = pd.NA
    frame["external_zone"] = frame["external_zone"].where(
        pd.notna(frame["external_zone"]),
        "boundary:" + frame["id"],
    )
    frame["external_zone"] = frame["external_zone"].astype(str)
    return frame.reset_index(drop=True)


def _boundary_prices_frame(value: pd.DataFrame) -> pd.DataFrame:
    frame = value.copy()
    if frame.empty:
        return pd.DataFrame(columns=[*BOUNDARY_PRICE_COLUMNS, "external_zone"])
    _require_columns(frame, BOUNDARY_PRICE_COLUMNS, "boundary_prices")
    frame["id"] = frame["id"].astype(str)
    frame["period"] = frame["period"].astype(int)
    frame["zone"] = frame["zone"].astype(str)
    for column in (
        "price_eur_per_mwh",
        "import_capacity_mwh",
        "export_capacity_mwh",
    ):
        frame[column] = frame[column].astype(float)
    if (frame["import_capacity_mwh"] < 0).any():
        raise ValueError("boundary_prices import_capacity_mwh must be non-negative")
    if (frame["export_capacity_mwh"] < 0).any():
        raise ValueError("boundary_prices export_capacity_mwh must be non-negative")
    if "external_zone" not in frame.columns:
        frame["external_zone"] = pd.NA
    frame["external_zone"] = frame["external_zone"].where(
        pd.notna(frame["external_zone"]),
        "boundary:" + frame["id"],
    )
    frame["external_zone"] = frame["external_zone"].astype(str)
    return frame.reset_index(drop=True)


def _normalize_order_common(frame: pd.DataFrame) -> None:
    frame["id"] = frame["id"].astype(str)
    frame["period"] = frame["period"].astype(int)
    frame["zone"] = frame["zone"].astype(str)
    frame["side"] = frame["side"].astype(str)
    unknown_sides = set(frame["side"]) - SIDES
    if unknown_sides:
        raise ValueError(f"unknown order sides {sorted(unknown_sides)}")
    frame["quantity_mwh"] = frame["quantity_mwh"].astype(float)
    if (frame["quantity_mwh"] < 0).any():
        raise ValueError("order quantities must be non-negative")
    frame["price_eur_per_mwh"] = frame["price_eur_per_mwh"].astype(float)


def _quantity_alias(frame: pd.DataFrame) -> pd.DataFrame:
    if "quantity_mwh" not in frame.columns and "quantity_mw" in frame.columns:
        frame = frame.rename(columns={"quantity_mw": "quantity_mwh"})
    return frame


def _ensure_named_index(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if name in frame.columns or isinstance(frame.index, pd.RangeIndex):
        return frame
    index_name = frame.index.name or name
    return frame.reset_index().rename(columns={index_name: name})


def _require_columns(
    frame: pd.DataFrame,
    columns: tuple[str, ...],
    table_name: str,
) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{table_name} missing required columns {missing}")


def _validate_ids(
    frame: pd.DataFrame,
    table_name: str,
    column: str,
    *,
    allow_repeated: bool = False,
) -> None:
    if frame.empty:
        return
    if (frame[column].astype(str).str.len() == 0).any():
        raise ValueError(f"{table_name}.{column} must be non-empty")
    if not allow_repeated and frame[column].duplicated().any():
        raise ValueError(f"{table_name}.{column} values must be unique")


def _infer_zones(*tables: pd.DataFrame) -> set[str]:
    zones: set[str] = set()
    for table in tables:
        if table.empty:
            continue
        if "zone" in table.columns:
            zones.update(str(value) for value in table["zone"].dropna())
        if "from_zone" in table.columns:
            zones.update(str(value) for value in table["from_zone"].dropna())
        if "to_zone" in table.columns:
            zones.update(str(value) for value in table["to_zone"].dropna())
    return zones


def _infer_periods(*tables: pd.DataFrame) -> set[int]:
    periods: set[int] = set()
    for table in tables:
        if table.empty or "period" not in table.columns:
            continue
        periods.update(int(value) for value in table["period"].dropna())
    return periods


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    if frame.empty:
        return []
    return frame.where(pd.notna(frame), None).to_dict(orient="records")


def _public_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in metadata.items()
        if not str(key).startswith("_") and _is_json_scalar_like(value)
    }


def _is_json_scalar_like(value: Any) -> bool:
    if isinstance(value, str | int | float | bool) or value is None:
        return True
    if isinstance(value, list | tuple):
        return all(_is_json_scalar_like(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_scalar_like(item) for key, item in value.items())
    return False
