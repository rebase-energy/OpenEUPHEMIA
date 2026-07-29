"""Reusable market topology: zones and interconnector pairs."""

from __future__ import annotations

from collections.abc import Iterable, Sequence


class System:
    """Bidding zones and the interconnectors between them.

    A ``System`` describes topology only; per-period capacities (NTCs) and
    orders belong to a :class:`~openeuphemia.core.Market`, so one system can
    be shared across delivery days.
    """

    def __init__(
        self,
        *,
        zones: Sequence[str] | None = None,
        interconnectors: Iterable[tuple[str, str]] | None = None,
    ) -> None:
        self._zones: list[str] = []
        self._interconnectors: list[tuple[str, str]] = []
        if zones:
            self.add_zones(zones)
        if interconnectors:
            self.add_interconnectors(interconnectors)

    @property
    def zones(self) -> tuple[str, ...]:
        return tuple(self._zones)

    @property
    def interconnectors(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._interconnectors)

    def add_zones(self, zones: Sequence[str]) -> None:
        for zone in zones:
            zone = str(zone)
            if not zone:
                raise ValueError("zone names must be non-empty")
            if zone in self._zones:
                raise ValueError(f"zone {zone!r} already exists")
            self._zones.append(zone)

    def add_interconnectors(self, pairs: Iterable[tuple[str, str]]) -> None:
        for pair in pairs:
            from_zone, to_zone = (str(zone) for zone in pair)
            self._require_zone(from_zone)
            self._require_zone(to_zone)
            if from_zone == to_zone:
                raise ValueError(
                    f"interconnector cannot connect {from_zone!r} to itself"
                )
            if self.has_interconnector(from_zone, to_zone):
                raise ValueError(
                    f"interconnector between {from_zone!r} and {to_zone!r} already exists"
                )
            self._interconnectors.append((from_zone, to_zone))

    def has_interconnector(self, from_zone: str, to_zone: str) -> bool:
        return (from_zone, to_zone) in self._interconnectors or (
            to_zone,
            from_zone,
        ) in self._interconnectors

    def _require_zone(self, zone: str) -> None:
        if zone not in self._zones:
            raise ValueError(f"unknown zone {zone!r}; add it with add_zones first")

    def __repr__(self) -> str:
        return (
            f"System(zones={len(self._zones)}, "
            f"interconnectors={len(self._interconnectors)})"
        )
