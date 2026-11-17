"""False-alarm control: Benjamini-Hochberg across slices, then persistence over time."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

from revwatch.models import BoolArray, FloatArray


def bh_reject(pvalues: FloatArray, q: float) -> BoolArray:
    """Benjamini-Hochberg at level `q`. NaN entries are not tests and are never rejected."""
    rejected = np.zeros(pvalues.shape, dtype=bool)
    scored = ~np.isnan(pvalues)
    m = int(scored.sum())
    if m == 0:
        return rejected
    p = pvalues[scored]
    order = np.argsort(p, kind="stable")
    passing = np.nonzero(p[order] <= q * np.arange(1, m + 1) / m)[0]
    if passing.size == 0:
        return rejected
    hit = np.zeros(m, dtype=bool)
    hit[order[: passing[-1] + 1]] = True
    rejected[scored] = hit
    return rejected


def persistent(
    passed: npt.NDArray[np.int8], tick: int, need: int, window: int
) -> npt.NDArray[np.int8]:
    """Direction of each slice that alarms at `tick`, else 0.

    `passed[s, t]` is +1 or -1 when slice s passed false-alarm control at tick t
    (above or below expected) and 0 otherwise. A slice alarms when it passes now
    and has passed in the same direction `need` times in the last `window` ticks.
    """
    now = passed[:, tick]
    lo = max(0, tick - window + 1)
    same = (passed[:, lo : tick + 1] == now[:, None]).sum(axis=1)
    return np.where((now != 0) & (same >= need), now, 0).astype(np.int8)
