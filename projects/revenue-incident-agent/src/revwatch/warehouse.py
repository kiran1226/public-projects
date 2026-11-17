"""Read access to a world, and the one place where each metric is defined.

The detectors, and later the investigation tools and the evidence verifier, all
compute metrics through this module, so a metric means the same thing everywhere.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self

import duckdb
import numpy as np

from revwatch.models import DIMENSIONS, BoolArray, FloatArray, Slice
from revwatch.slices import enumerate_slices

CALENDAR_KINDS = ("public_holiday", "planned_promotion", "planned_campaign", "maintenance")
SCOPE_COLUMNS = ("country", "platform", "channel", "category", "payment_method")
# The grain of each fact table: it has one row per hour and combination of these.
TABLE_DIMS: dict[str, tuple[str, ...]] = {
    "traffic": ("country", "platform", "channel"),
    "payments": ("country", "platform", "payment_method"),
    "orders": ("country", "platform", "channel", "category"),
}


@dataclass(frozen=True)
class MetricDef:
    """A metric: SUM(num) for counts, SUM(num) / SUM(den) for rates.

    `num` and `den` are (table, SQL expression). They are constants of this
    module, never user input, so they are safe to place in a query.
    """

    name: str
    kind: Literal["count", "rate"]
    num: tuple[str, str]
    den: tuple[str, str] | None
    dims: tuple[str, ...]
    scale: Literal["log_count", "binomial", "inverse_sqrt"]
    min_volume: float
    definition: str
    why: str
    detected: bool = True


_FUNNEL = ("country", "platform", "channel")
_ORDER = ("country", "platform", "channel", "category")

METRICS: dict[str, MetricDef] = {
    m.name: m
    for m in (
        MetricDef(
            "sessions", "count", ("traffic", "sessions"), None, _FUNNEL, "log_count", 40.0,
            "Visits per hour",
            "Separates 'fewer visitors' from 'visitors not buying'",
        ),
        MetricDef(
            "checkout_conversion", "rate", ("orders", "orders"), ("traffic", "sessions"),
            _FUNNEL, "binomial", 300.0,
            "Orders / sessions",
            "Catches site and app breakage that revenue shows late",
        ),
        MetricDef(
            "payment_success_rate", "rate", ("payments", "successes"), ("payments", "attempts"),
            ("country", "platform", "payment_method"), "binomial", 40.0,
            "Payment successes / attempts",
            "Isolates provider failure from everything upstream",
        ),
        MetricDef(
            "discount_rate", "rate", ("orders", "discount"), ("orders", "gross_revenue"),
            _ORDER, "inverse_sqrt", 800.0,
            "Discount / gross revenue",
            "Catches coupon errors, where orders rise and revenue looks healthy",
        ),
        MetricDef(
            "gross_margin_rate", "rate",
            ("orders", "gross_revenue - discount - cost"), ("orders", "gross_revenue - discount"),
            _ORDER, "inverse_sqrt", 800.0,
            "(Net revenue - cost) / net revenue",
            "Catches price errors that raise volume",
        ),
        MetricDef(
            "net_revenue", "count", ("orders", "gross_revenue - discount"), None,
            _ORDER, "log_count", 0.0,
            "Gross revenue - discount",
            "Used to size impact, not to detect: it lags and mixes every cause",
            detected=False,
        ),
    )
}  # fmt: skip


@dataclass(frozen=True)
class Panel:
    """One metric as dense arrays, one row per slice and one column per hour.

    `num` and `den` are NaN where a source table did not have every row the slice
    is made of for that hour: late data is unknown, not zero and not "a bit low".
    `den` is all ones for count metrics.
    """

    metric: MetricDef
    slices: tuple[Slice, ...]
    num: FloatArray
    den: FloatArray

    @property
    def value(self) -> FloatArray:
        """The metric: the count, or the rate (NaN where the denominator is zero)."""
        if self.metric.kind == "count":
            return self.num
        out = np.full(self.num.shape, np.nan)
        np.divide(self.num, self.den, out=out, where=self.den > 0)
        return out


class Warehouse:
    """Read-only access to one generated world."""

    def __init__(self, path: Path) -> None:
        if not path.exists():
            raise FileNotFoundError(f"world not found: {path}. Run `revwatch generate` first.")
        try:
            self._con = duckdb.connect(str(path), read_only=True)
            # One thread, so that a sum adds its rows in stored order. With several, how
            # rows fall to threads depends on how the file was written, which differs from
            # one build of the same world to the next: a figure can then differ in its last
            # digit, and a recorded investigation no longer matches what the tools return.
            self._con.execute("SET threads = 1")
            rows = self._con.execute("SELECT key, value FROM meta").fetchall()
            meta = {str(k): str(v) for k, v in rows}
            self.start = datetime.fromisoformat(meta["start"])
            self.n_hours = int(meta["n_hours"])
            self.world_hash = meta["world_hash"]
        except (duckdb.Error, KeyError) as exc:
            raise ValueError(
                f"{path} is not a revwatch world. Build one with `revwatch generate`."
            ) from exc
        self.path = path
        self._cache: dict[tuple[str, str, tuple[str, ...]], FloatArray] = {}

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._con.close()

    def hour(self, tick: int) -> datetime:
        return self.start + timedelta(hours=tick)

    def tick(self, when: datetime) -> int:
        return int((when - self.start).total_seconds() // 3600)

    def panel(self, metric: MetricDef) -> Panel:
        slices = enumerate_slices(metric.dims)
        num = self._rows(metric.num, slices)
        complete = self._complete(metric.num[0], slices)
        if metric.den is None:
            den = np.ones_like(num)
        else:
            den = self._rows(metric.den, slices)
            complete = complete & self._complete(metric.den[0], slices)
        num[~complete] = np.nan
        den[~complete] = np.nan
        return Panel(metric, tuple(slices), num, den)

    def calendar_flags(self, slices: Sequence[Slice]) -> dict[str, BoolArray]:
        """Per calendar kind, which slice-hours overlap a planned event.

        An event applies to a slice unless they name different values of the
        same dimension, so a holiday in Spain flags Spain and the overall series.
        """
        flags = {kind: np.zeros((len(slices), self.n_hours), dtype=bool) for kind in CALENDAR_KINDS}
        columns = ", ".join(SCOPE_COLUMNS)
        rows = self._con.execute(
            f"SELECT kind, start_ts, end_ts, {columns} FROM calendar"
        ).fetchall()
        for kind, start_ts, end_ts, *scope_values in rows:
            scope = {
                c: v for c, v in zip(SCOPE_COLUMNS, scope_values, strict=True) if v is not None
            }
            lo = max(0, self.tick(start_ts))
            hi = min(self.n_hours, self.tick(end_ts))
            for i, candidate in enumerate(slices):
                have = candidate.as_dict()
                if all(have.get(dim, value) == value for dim, value in scope.items()):
                    flags[str(kind)][i, lo:hi] = True
        return flags

    # --- what the investigation tools read ---------------------------------------

    def entities(self) -> dict[str, set[str]]:
        """What can be acted on, by kind: provider, coupon, release, sku, campaign."""
        found: dict[str, set[str]] = {}
        for kind, entity in self._con.execute("SELECT kind, entity FROM entities").fetchall():
            found.setdefault(str(kind), set()).add(str(entity))
        return found

    def series(
        self, metric: MetricDef, where: Mapping[str, str], lo: int, hi: int
    ) -> tuple[FloatArray, FloatArray]:
        """Numerator and denominator per hour for ticks lo..hi-1, for any cut of the metric.

        `where` may name any of the metric's dimensions and, for payment success,
        the provider. The denominator of a count is 1.
        """
        num = self._per_hour(metric.num, self._filters(metric, where), lo, hi)
        if metric.den is None:
            return num, np.ones_like(num)
        return num, self._per_hour(metric.den, self._filters(metric, where), lo, hi)

    def split(
        self, metric: MetricDef, where: Mapping[str, str], by: str, lo: int, hi: int
    ) -> dict[str, tuple[float, float]]:
        """Numerator and denominator over ticks lo..hi-1 for each value of `by`."""
        if by not in self._columns(metric) or by in where:
            raise ValueError(
                f"{metric.name} cannot be split by {by!r}; choose from "
                f"{', '.join(c for c in self._columns(metric) if c not in where)}"
            )
        filters = self._filters(metric, where)
        num = self._per_value(metric.num, filters, by, lo, hi)
        den = {} if metric.den is None else self._per_value(metric.den, filters, by, lo, hi)
        return {
            value: (num.get(value, 0.0), 1.0 if metric.den is None else den.get(value, 0.0))
            for value in sorted(set(num) | set(den))
        }

    def changes(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Change-log entries with start <= time < end, oldest first."""
        return self._records(
            "SELECT * FROM changes WHERE ts >= ? AND ts < ? ORDER BY ts, change_id", [start, end]
        )

    def change(self, change_id: str) -> dict[str, Any] | None:
        found = self._records("SELECT * FROM changes WHERE change_id = ?", [change_id])
        return found[0] if found else None

    def events(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Calendar events that overlap start..end, oldest first."""
        return self._records(
            "SELECT * FROM calendar WHERE start_ts < ? AND end_ts > ? ORDER BY start_ts, event_id",
            [end, start],
        )

    def _records(self, sql: str, params: Sequence[Any]) -> list[dict[str, Any]]:
        cursor = self._con.execute(sql, list(params))
        names = [column[0] for column in cursor.description]
        return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def _columns(self, metric: MetricDef) -> tuple[str, ...]:
        """The columns a tool may filter or split `metric` by."""
        tables = {metric.num[0]} | ({metric.den[0]} if metric.den else set())
        return (*metric.dims, "provider") if tables == {"payments"} else metric.dims

    def _filters(self, metric: MetricDef, where: Mapping[str, str]) -> list[tuple[str, str]]:
        """`where` as (column, value) pairs, checked: column names go into the SQL text."""
        allowed = self._columns(metric)
        for column, value in where.items():
            if column not in allowed:
                raise ValueError(
                    f"{metric.name} has no dimension {column!r}; it has {', '.join(allowed)}"
                )
            known = (
                self.entities().get("provider", set())
                if column == "provider"
                else DIMENSIONS[column]
            )
            if value not in known:
                raise ValueError(f"unknown {column} value {value!r}")
        return sorted(where.items())

    def _where(self, filters: Sequence[tuple[str, str]], lo: int, hi: int) -> tuple[str, list[Any]]:
        clauses = ["ts >= ?", "ts < ?", *(f"{column} = ?" for column, _ in filters)]
        return " AND ".join(clauses), [self.hour(lo), self.hour(hi), *(v for _, v in filters)]

    def _per_hour(
        self, source: tuple[str, str], filters: Sequence[tuple[str, str]], lo: int, hi: int
    ) -> FloatArray:
        table, expr = source
        where, params = self._where(filters, lo, hi)
        rows = self._con.execute(
            f"SELECT {self._hour_sql()} AS h, SUM({expr}) FROM {table} WHERE {where} GROUP BY h",
            params,
        ).fetchall()
        out = np.zeros(hi - lo)
        for tick, value in rows:
            out[int(tick) - lo] = float(value)
        return out

    def _per_value(
        self,
        source: tuple[str, str],
        filters: Sequence[tuple[str, str]],
        by: str,
        lo: int,
        hi: int,
    ) -> dict[str, float]:
        table, expr = source
        where, params = self._where(filters, lo, hi)
        rows = self._con.execute(
            f"SELECT {by}, SUM({expr}) FROM {table} WHERE {where} GROUP BY {by}", params
        ).fetchall()
        return {str(value): float(total) for value, total in rows}

    def _complete(self, table: str, slices: Sequence[Slice]) -> BoolArray:
        """Per slice and hour: whether `table` has every row the slice is made of.

        The tables are dense, so the number of rows a slice should have in an hour
        is known: one per combination of the table's other dimensions.
        """
        sizes = {dim: len(DIMENSIONS[dim]) for dim in TABLE_DIMS[table]}
        wanted = np.array(
            [
                np.prod([n for dim, n in sizes.items() if dim not in s.as_dict()], dtype=np.int64)
                for s in slices
            ]
        )
        return self._rows((table, "1"), slices) >= wanted[:, None]

    def _hour_sql(self) -> str:
        return f"CAST(date_diff('hour', TIMESTAMP '{self.start:%Y-%m-%d %H:%M:%S}', ts) AS INTEGER)"

    def _grouped(self, table: str, expr: str, dims: tuple[str, ...]) -> FloatArray:
        """SUM(expr) by hour and `dims`: shape (product of dim sizes, n_hours), zero-filled."""
        key = (table, expr, dims)
        if key not in self._cache:
            sizes = [len(DIMENSIONS[d]) for d in dims]
            codes = "".join(
                f", CAST(list_position({list(DIMENSIONS[d])!r}, {d}) - 1 AS INTEGER) AS c{i}"
                for i, d in enumerate(dims)
            )
            sql = (
                f"SELECT {self._hour_sql()} AS h{codes}, SUM({expr}) AS v FROM {table} GROUP BY ALL"
            )
            res = self._con.execute(sql).fetchnumpy()
            row = np.zeros(len(res["h"]), dtype=np.int64)
            for i, size in enumerate(sizes):
                row = row * size + np.asarray(res[f"c{i}"], dtype=np.int64)
            out = np.zeros((int(np.prod(sizes)) if sizes else 1, self.n_hours))
            out[row, np.asarray(res["h"], dtype=np.int64)] = np.asarray(res["v"], dtype=np.float64)
            self._cache[key] = out
        return self._cache[key]

    def _rows(self, source: tuple[str, str], slices: Sequence[Slice]) -> FloatArray:
        table, expr = source
        out = np.zeros((len(slices), self.n_hours))
        for i, candidate in enumerate(slices):
            dims = tuple(dim for dim, _ in candidate.items)
            row = 0
            for dim, value in candidate.items:
                row = row * len(DIMENSIONS[dim]) + DIMENSIONS[dim].index(value)
            out[i] = self._grouped(table, expr, dims)[row]
        return out
