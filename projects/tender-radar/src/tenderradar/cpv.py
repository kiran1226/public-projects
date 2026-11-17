"""Category codes (CPV, the EU's Common Procurement Vocabulary).

A code is eight digits read left to right from broad to narrow: 38 is laboratory,
optical and precision equipment, 384 is instruments for checking physical
characteristics, 38434 is analysers. Trailing zeros mean "this whole level".
"""

from __future__ import annotations

DEPTHS = (2, 3, 4, 5, 6, 7, 8)


def valid(code: str) -> bool:
    return isinstance(code, str) and len(code) == 8 and code.isdigit()


def stem(code: str) -> str:
    """The code without its trailing zeros, never shorter than the two-digit division."""
    return code.rstrip("0").ljust(2, "0") if valid(code) else ""


def shared_depth(a: str, b: str) -> int:
    """How many leading digits two codes share, counted only over their stems.

    38434000 and 38434540 share 5 digits (the narrower code lies inside the broader
    one). 38434000 and 38430000 share 4.
    """
    stem_a, stem_b = stem(a), stem(b)
    depth = 0
    for digit_a, digit_b in zip(stem_a, stem_b, strict=False):
        if digit_a != digit_b:
            break
        depth += 1
    return depth if depth >= 2 else 0


def prefix(code: str, depth: int) -> str:
    return code[:depth] if valid(code) else ""


def in_divisions(codes: list[str], divisions: tuple[str, ...]) -> bool:
    return any(code[:2] in divisions for code in codes if valid(code))
