"""Shared types: dimensions, array aliases and slices."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]
BoolArray = npt.NDArray[np.bool_]

DIMENSIONS: dict[str, tuple[str, ...]] = {
    "country": ("DE", "FR", "NL", "AT", "PL", "ES"),
    "platform": ("web_desktop", "web_mobile", "app_ios", "app_android"),
    "channel": ("direct", "organic", "paid_search", "email", "affiliate"),
    "payment_method": ("card", "wallet", "bank_transfer", "pay_later", "direct_debit"),
    "category": (
        "furniture",
        "lighting",
        "textiles",
        "kitchen",
        "decor",
        "garden",
        "storage",
        "kids",
    ),
}


@dataclass(frozen=True, order=True)
class Slice:
    """A cut of the data: dimension=value pairs. The empty slice is everything."""

    items: tuple[tuple[str, str], ...] = ()

    @classmethod
    def of(cls, **dims: str) -> Slice:
        return cls.from_dict(dims)

    @classmethod
    def from_dict(cls, dims: Mapping[str, str]) -> Slice:
        for dim, value in dims.items():
            if dim not in DIMENSIONS:
                raise ValueError(f"unknown dimension {dim!r}")
            if value not in DIMENSIONS[dim]:
                raise ValueError(f"unknown {dim} value {value!r}")
        return cls(tuple(sorted(dims.items())))

    def as_dict(self) -> dict[str, str]:
        return dict(self.items)

    @property
    def key(self) -> str:
        return "|".join(f"{dim}={value}" for dim, value in self.items) or "all"

    def contains(self, other: Slice) -> bool:
        """True when `other` is this slice or a narrower cut of it."""
        return set(self.items) <= set(other.items)

    def related(self, other: Slice) -> bool:
        """True when one slice contains the other."""
        return self.contains(other) or other.contains(self)

    def project(self, dims: Collection[str]) -> Slice:
        """This slice as seen through `dims` only: the other dimensions are dropped."""
        return Slice(tuple(item for item in self.items if item[0] in dims))


Member = tuple[str, Slice, int]  # (metric, slice, direction)


@dataclass(frozen=True)
class Alarm:
    """One slice of one metric that passed false-alarm control at a tick."""

    metric: str
    slice: Slice
    tick: int
    direction: int  # +1 above expected, -1 below
    strength: float  # signed, from the detector
    deviation: float  # numerator deviation against expected, over the evidence window

    @property
    def member(self) -> Member:
        return (self.metric, self.slice, self.direction)


@dataclass
class Incident:
    """A group of linked alarms. `metric` and `slice` name it and are fixed when it opens.

    The name is what the alert said when it was raised, so it does not change.
    `roots` is the current reading: per metric, the slices that nothing else
    explains. An investigation starts from the roots.

    An incident is an alert that was raised, so it is never deleted. When a later
    alarm shows that two open incidents are one, the younger is closed and points
    at the survivor through `merged_into`.
    """

    incident_id: str
    opened_tick: int
    metric: str
    slice: Slice
    direction: int
    last_alarm_tick: int
    closed_tick: int | None = None
    members: set[Member] = field(default_factory=set)  # every member it has ever had
    # Per member, the tick of its last alarm; per root, also the last tick it was the root.
    last_seen: dict[Member, int] = field(default_factory=dict)
    roots: set[Member] = field(default_factory=set)  # as of the last tick it alarmed
    merged_into: str | None = None
