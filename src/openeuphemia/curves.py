"""Aggregated bid curves for the Pythonic market-building API."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from openeuphemia.core import DEMAND, SIDES, SUPPLY

_QUANTITY_TOLERANCE = 1e-9


@dataclass(frozen=True)
class BidCurve:
    """A cumulative price/volume bid curve for one zone, period, and side.

    Points pair a price with the cumulative volume available at that price.
    The curve is interpreted stepwise: the incremental volume between two
    consecutive points is offered at the price of the point where that
    cumulative volume is reached. Linearly interpolated curves are not
    supported yet.

    Supply curves must have non-decreasing prices, demand curves
    non-increasing prices; cumulative volumes are non-decreasing for both.
    """

    prices: tuple[float, ...]
    cumulative_volumes: tuple[float, ...]

    def __init__(
        self,
        *,
        prices: Sequence[float],
        cumulative_volumes: Sequence[float],
    ) -> None:
        object.__setattr__(self, "prices", tuple(float(price) for price in prices))
        object.__setattr__(
            self,
            "cumulative_volumes",
            tuple(float(volume) for volume in cumulative_volumes),
        )
        self._validate()

    def _validate(self) -> None:
        if len(self.prices) != len(self.cumulative_volumes):
            raise ValueError(
                "prices and cumulative_volumes must have the same length, got "
                f"{len(self.prices)} and {len(self.cumulative_volumes)}"
            )
        if not self.prices:
            raise ValueError("bid curve requires at least one point")
        for name, values in (
            ("prices", self.prices),
            ("cumulative_volumes", self.cumulative_volumes),
        ):
            if not all(isfinite(value) for value in values):
                raise ValueError(f"bid curve {name} must be finite numbers")
        if any(volume < 0 for volume in self.cumulative_volumes):
            raise ValueError("cumulative_volumes must be non-negative")
        if any(
            later < earlier
            for earlier, later in zip(
                self.cumulative_volumes, self.cumulative_volumes[1:]
            )
        ):
            raise ValueError("cumulative_volumes must be non-decreasing")
        non_decreasing = self._prices_non_decreasing()
        non_increasing = self._prices_non_increasing()
        if not (non_decreasing or non_increasing):
            raise ValueError(
                "bid curve prices must be monotone: non-decreasing for supply "
                "or non-increasing for demand"
            )

    def _prices_non_decreasing(self) -> bool:
        return all(
            later >= earlier for earlier, later in zip(self.prices, self.prices[1:])
        )

    def _prices_non_increasing(self) -> bool:
        return all(
            later <= earlier for earlier, later in zip(self.prices, self.prices[1:])
        )

    def __len__(self) -> int:
        return len(self.prices)

    @property
    def volumes(self) -> tuple[float, ...]:
        """Incremental volume of each curve point."""

        previous = 0.0
        increments = []
        for volume in self.cumulative_volumes:
            increments.append(volume - previous)
            previous = volume
        return tuple(increments)

    @property
    def total_volume(self) -> float:
        return self.cumulative_volumes[-1] if self.cumulative_volumes else 0.0

    def segments(self, side: str) -> list[tuple[float, float]]:
        """Return ``(quantity, price)`` order segments for ``side``.

        Zero-quantity points are dropped; they only anchor the curve.
        """

        self._validate_side(side)
        result = []
        for quantity, price in zip(self.volumes, self.prices):
            if quantity > _QUANTITY_TOLERANCE:
                result.append((quantity, price))
        return result

    def to_orders(
        self,
        *,
        zone: str,
        period: int,
        side: str,
        id_prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """Lower the curve to rows for the ``Market.orders`` component table."""

        prefix = id_prefix or f"{zone}_p{period}_{side}"
        return [
            {
                "id": f"{prefix}_{index}",
                "period": int(period),
                "zone": zone,
                "side": side,
                "quantity_mwh": quantity,
                "price_eur_per_mwh": price,
            }
            for index, (quantity, price) in enumerate(self.segments(side))
        ]

    def _validate_side(self, side: str) -> None:
        if side not in SIDES:
            raise ValueError(f"side must be one of {sorted(SIDES)}, got {side!r}")
        if side == SUPPLY and not self._prices_non_decreasing():
            raise ValueError("supply bid curve prices must be non-decreasing")
        if side == DEMAND and not self._prices_non_increasing():
            raise ValueError("demand bid curve prices must be non-increasing")

    def plot(self, ax: Any = None, *, side: str | None = None, **kwargs: Any) -> Any:
        """Plot the cumulative curve as a step function on ``ax``."""

        import matplotlib.pyplot as plt

        if ax is None:
            _, ax = plt.subplots()
        volumes = (0.0, *self.cumulative_volumes)
        prices = (self.prices[0], *self.prices)
        kwargs.setdefault("where", "pre")
        ax.step(volumes, prices, **kwargs)
        ax.set_xlabel("Cumulative volume (MWh)")
        ax.set_ylabel("Price (EUR/MWh)")
        if side:
            ax.set_title(f"{side.capitalize()} curve")
        return ax
