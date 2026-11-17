"""Hand-built panels for detector tests: a seasonal conversion rate with known noise."""

from collections.abc import Sequence

import numpy as np

from revwatch.models import BoolArray
from revwatch.slices import enumerate_slices
from revwatch.warehouse import CALENDAR_KINDS, METRICS, Panel


def synthetic_panel(
    seed: int = 0,
    n_hours: int = 1512,
    event_hours: Sequence[tuple[int, int] | tuple[int, int, float]] = (),
) -> Panel:
    """Checkout conversion near 5% for 90 independent slices, with daily and weekly shape.

    `event_hours` are (lo, hi) or (lo, hi, factor) windows in which the rate is
    multiplied by `factor` (1.3 when not given) for every slice, standing in for a
    planned promotion.
    """
    metric = METRICS["checkout_conversion"]
    slices = enumerate_slices(metric.dims)
    rng = np.random.default_rng(seed)
    ticks = np.arange(n_hours)
    season = 1.0 + 0.5 * np.sin(2 * np.pi * (ticks % 24) / 24) + 0.2 * ((ticks // 24) % 7 >= 5)
    level = rng.uniform(0.6, 1.4, size=(len(slices), 1))
    den = rng.poisson(20_000 * level * season[None, :]).astype(np.float64)
    weekly = 1.0 + 0.1 * np.sin(2 * np.pi * (ticks % 168) / 168)
    rate = 0.05 * rng.uniform(0.8, 1.2, size=(len(slices), 1)) * weekly[None, :]
    for lo, hi, *factor in event_hours:
        rate[:, lo:hi] *= factor[0] if factor else 1.3
    num = rng.binomial(den.astype(np.int64), rate).astype(np.float64)
    return Panel(metric, tuple(slices), num, den)


def with_factor(panel: Panel, rows: int | slice, lo: int, hi: int, factor: float) -> Panel:
    """A copy of `panel` with the numerator multiplied by `factor` in rows x [lo, hi)."""
    num = panel.num.copy()
    num[rows, lo:hi] = np.rint(num[rows, lo:hi] * factor)
    return Panel(panel.metric, panel.slices, num, panel.den)


def no_flags(panel: Panel) -> dict[str, BoolArray]:
    return {kind: np.zeros(panel.num.shape, dtype=bool) for kind in CALENDAR_KINDS}


def no_exclusions(panel: Panel) -> BoolArray:
    return np.zeros(panel.num.shape, dtype=bool)


def overdispersed_counts(seed: int = 0, n_hours: int = 1512) -> Panel:
    """Sessions for 90 slices whose noise is well above Poisson, as real traffic is."""
    metric = METRICS["sessions"]
    slices = enumerate_slices(metric.dims)
    rng = np.random.default_rng(seed)
    ticks = np.arange(n_hours)
    season = 1.0 + 0.5 * np.sin(2 * np.pi * (ticks % 24) / 24) + 0.2 * ((ticks // 24) % 7 >= 5)
    level = rng.uniform(0.6, 1.4, size=(len(slices), 1))
    lam = 3_000 * level * season[None, :] * rng.gamma(60.0, 1.0 / 60.0, size=(len(slices), n_hours))
    num = rng.poisson(lam).astype(np.float64)
    return Panel(metric, tuple(slices), num, np.ones_like(num))


def quiet_rate(seed: int = 0, n_hours: int = 1512) -> Panel:
    """A discount rate near 6% whose hour-to-hour noise is far below 1 / sqrt(revenue)."""
    metric = METRICS["discount_rate"]
    slices = enumerate_slices(metric.dims)
    rng = np.random.default_rng(seed)
    ticks = np.arange(n_hours)
    season = 1.0 + 0.5 * np.sin(2 * np.pi * (ticks % 24) / 24)
    den = 5_000 * rng.uniform(0.6, 1.4, size=(len(slices), 1)) * season[None, :]
    rate = 0.06 + rng.normal(0.0, 0.002, size=den.shape)
    return Panel(metric, tuple(slices), den * rate, den)


def mixed_volume_counts(seed: int = 0, n_hours: int = 1512) -> Panel:
    """Sessions for 90 slices from 300 to 15,000 an hour, all with 5% relative noise.

    Against a Poisson scale the large slices look far noisier than the small ones.
    """
    metric = METRICS["sessions"]
    slices = enumerate_slices(metric.dims)
    rng = np.random.default_rng(seed)
    ticks = np.arange(n_hours)
    season = 1.0 + 0.5 * np.sin(2 * np.pi * (ticks % 24) / 24) + 0.2 * ((ticks // 24) % 7 >= 5)
    level = np.geomspace(300, 15_000, len(slices))[:, None]
    lam = level * season[None, :] * rng.lognormal(0.0, 0.05, size=(len(slices), n_hours))
    num = rng.poisson(lam).astype(np.float64)
    return Panel(metric, tuple(slices), num, np.ones_like(num))
