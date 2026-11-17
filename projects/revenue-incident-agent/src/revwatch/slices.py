"""Which slices a metric is scored on: overall, each single value, each pair."""

from __future__ import annotations

from itertools import combinations, product

from revwatch.models import DIMENSIONS, Slice


def enumerate_slices(dims: tuple[str, ...]) -> list[Slice]:
    """The empty slice, then every single value, then every pair, in a fixed order."""
    slices = [Slice()]
    for size in (1, 2):
        for chosen in combinations(dims, size):
            for values in product(*(DIMENSIONS[d] for d in chosen)):
                slices.append(Slice.from_dict(dict(zip(chosen, values, strict=True))))
    return slices
