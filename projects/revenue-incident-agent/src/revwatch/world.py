"""Synthetic world generator.

Builds hourly facts for a fictional retailer, a change log and a calendar, and
injects the scenarios. The same seed and scenarios always give the same world.

The funnel is simulated in order, so an injected fault has consistent effects:
sessions -> checkouts started -> payment attempts -> payment successes -> orders.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from revwatch.models import DIMENSIONS, FloatArray
from revwatch.scenarios import PARAM_DIMS, ChangeSpec, Label, Scenario, save_labels

SIZES = {dim: len(values) for dim, values in DIMENSIONS.items()}
C, P, H, M, K = (
    SIZES["country"],
    SIZES["platform"],
    SIZES["channel"],
    SIZES["payment_method"],
    SIZES["category"],
)

BASE_SESSIONS_PER_HOUR = 12_000.0
COUNTRY_SHARE = np.array([0.34, 0.22, 0.12, 0.08, 0.10, 0.14])
PLATFORM_SHARE = np.array([0.30, 0.34, 0.20, 0.16])
CHANNEL_SHARE = np.array([0.25, 0.30, 0.22, 0.10, 0.13])
CATEGORY_SHARE = np.array([0.22, 0.14, 0.13, 0.15, 0.12, 0.09, 0.09, 0.06])
_HOUR_RAW = np.array(
    [0.25, 0.18, 0.14, 0.12, 0.14, 0.22, 0.40, 0.65, 0.85, 0.95, 1.00, 1.05]
    + [1.10, 1.05, 1.00, 1.00, 1.05, 1.15, 1.35, 1.55, 1.65, 1.45, 0.95, 0.50]
)
HOUR_SHAPE = _HOUR_RAW / _HOUR_RAW.mean()
WEEKDAY_SHAPE = np.array([1.00, 0.97, 0.96, 0.98, 1.02, 1.10, 1.18])  # Monday first
DAILY_GROWTH = 0.0008

CHECKOUT_RATE = 0.085
PLATFORM_CHECKOUT = np.array([1.15, 0.80, 1.20, 1.05])
CHANNEL_CHECKOUT = np.array([1.10, 0.90, 1.00, 1.40, 0.80])
COMPLETION_RATE = 0.62
METHOD_MIX = np.array(  # rows: country, columns: payment method
    [
        [0.30, 0.30, 0.15, 0.20, 0.05],  # DE
        [0.60, 0.20, 0.05, 0.10, 0.05],  # FR
        [0.20, 0.15, 0.50, 0.10, 0.05],  # NL
        [0.35, 0.25, 0.15, 0.20, 0.05],  # AT
        [0.30, 0.15, 0.40, 0.10, 0.05],  # PL
        [0.60, 0.25, 0.05, 0.05, 0.05],  # ES
    ]
)
METHOD_SUCCESS = np.array([0.93, 0.96, 0.90, 0.94, 0.92])
AOV = np.array([240.0, 85.0, 60.0, 95.0, 45.0, 110.0, 70.0, 55.0])  # EUR per order, by category
COST_SHARE = np.array([0.58, 0.52, 0.50, 0.55, 0.48, 0.56, 0.54, 0.50])
BASE_DISCOUNT = 0.05
CHANNEL_DISCOUNT_ADD = np.array([0.0, 0.0, 0.01, 0.05, 0.02])

PROVIDERS = ("psp_alpha", "psp_beta", "psp_gamma", "psp_delta", "psp_epsilon")
COUPONS = (
    "WELCOME10", "SOMMER15", "MOEBEL25", "HERBST10", "LICHT5",
    "FREESHIP", "APP5", "VIP20", "KIDS10", "GARTEN15",
)  # fmt: skip
CAMPAIGNS: tuple[tuple[str, str, str | None], ...] = (
    ("ps-de-brand", "paid_search", "DE"),
    ("ps-fr-generic", "paid_search", "FR"),
    ("ps-es-brand-q4", "paid_search", "ES"),
    ("ps-nl-shopping", "paid_search", "NL"),
    ("ps-pl-generic", "paid_search", "PL"),
    ("aff-eu-cashback", "affiliate", None),
    ("mail-weekly", "email", None),
    ("mail-winback", "email", None),
)
TAGS = ("gtm-web", "gtm-app", "pixel-social", "pixel-search")
CONFIGS = ("cdn-cache", "search-ranking", "shipping-rates", "tax-tables", "feature-flags")
RELEASE_PREFIX = {
    "web_desktop": "web",
    "web_mobile": "web",
    "app_ios": "ios",
    "app_android": "android",
}
RELEASE_NOTES = (
    "search suggestions tweak",
    "image lazy loading",
    "wishlist sync fix",
    "copy changes on product page",
    "dependency updates",
    "basket badge fix",
)
COUPON_NOTES = ("created", "end date extended", "usage limit raised", "description edited")
CAMPAIGN_NOTES = ("bid adjusted", "creative rotated", "budget increased", "audience updated")
NOISE_KINDS = (
    "release",
    "coupon",
    "price",
    "provider_status",
    "campaign",
    "tracking_tag",
    "config",
)
NOISE_WEIGHTS = (0.22, 0.10, 0.30, 0.03, 0.15, 0.05, 0.15)
NOISE_CHANGES_PER_DAY = 15
SCENARIO_ACTOR = {
    "release": "deploy-bot",
    "coupon": "m.keller",
    "price": "pricing-import",
    "provider_status": "status-feed",
    "campaign": "campaign-sync",
    "tracking_tag": "a.novak",
    "config": "ops-oncall",
}
ENTITY_KIND = {
    "release": "release",
    "coupon": "coupon",
    "price": "sku",
    "provider_status": "provider",
    "campaign": "campaign",
}
SCOPE_COLUMNS = ("country", "platform", "channel", "category", "payment_method")


def provider_for(country: str, method: str) -> str:
    """Which (fictional) payment provider handles a method in a country."""
    if method == "card":
        return "psp_alpha" if country in ("DE", "NL", "AT") else "psp_beta"
    return {
        "wallet": "psp_gamma",
        "bank_transfer": "psp_delta",
        "pay_later": "psp_epsilon",
        "direct_debit": "psp_delta",
    }[method]


PROVIDER_TABLE = np.array(
    [[provider_for(c, m) for m in DIMENSIONS["payment_method"]] for c in DIMENSIONS["country"]]
)


@dataclass(frozen=True)
class WorldConfig:
    seed: int
    start: datetime = datetime(2026, 6, 1)
    days: int = 140


def generate_world(
    config: WorldConfig, scenarios: Sequence[Scenario], out_path: Path
) -> list[Label]:
    """Build a world, write it to `out_path`, and return the ground-truth labels."""
    if config.start.weekday() != 0 or config.start != config.start.replace(
        hour=0, minute=0, second=0, microsecond=0
    ):
        raise ValueError("a world must start on a Monday at 00:00")
    rng = np.random.default_rng(config.seed)
    n_hours = config.days * 24

    params = _scenario_params(config, scenarios, n_hours)
    facts = _simulate(rng, params, n_hours)
    tables = _fact_tables(config, facts, n_hours)
    for scenario in scenarios:
        if scenario.drop_table is not None:
            frame = tables[scenario.drop_table]
            keep = (frame["ts"] < scenario.start) | (frame["ts"] >= scenario.end)
            tables[scenario.drop_table] = frame[keep]
    changes, change_ids = _changes(rng, config, scenarios, n_hours)
    calendar = _calendar(scenarios)
    digest = _digest(facts, changes, calendar, scenarios)
    _write(out_path, config, n_hours, tables, changes, calendar, digest)

    labels = [
        Label(
            scenario_id=s.id,
            kind=s.kind,
            cause=s.cause,
            start=s.start,
            end=s.end,
            slice=s.slice,
            labelled=s.labelled,
            change_id=change_ids.get(s.id),
            mitigation=s.mitigation,
            drop_table=s.drop_table,
            moves=s.moves(),
        )
        for s in scenarios
    ]
    save_labels(labels, out_path.with_suffix(".labels.json"))
    return labels


def _tick(config: WorldConfig, when: datetime) -> int:
    return int((when - config.start).total_seconds() // 3600)


def _scenario_params(
    config: WorldConfig, scenarios: Sequence[Scenario], n_hours: int
) -> dict[str, FloatArray]:
    """One array per generator parameter, with every scenario effect applied."""
    params: dict[str, FloatArray] = {}
    for name, dims in PARAM_DIMS.items():
        shape = (n_hours, *(SIZES[d] for d in dims))
        params[name] = np.zeros(shape) if name.endswith("_add") else np.ones(shape)
    for scenario in scenarios:
        t0, t1 = _tick(config, scenario.start), _tick(config, scenario.end)
        if t0 < 0 or t1 > n_hours:
            raise ValueError(
                f"{scenario.id}: outside the world "
                f"({config.start:%Y-%m-%d} plus {config.days} days)"
            )
        for effect in scenario.effects:
            _apply(
                params[effect.param],
                PARAM_DIMS[effect.param],
                scenario.scope_of(effect),
                t0,
                t1,
                scenario.ramp_hours,
                effect.value,
                additive=effect.param.endswith("_add"),
            )
    return params


def _apply(
    arr: FloatArray,
    dims: tuple[str, ...],
    scope: dict[str, str],
    t0: int,
    t1: int,
    ramp_hours: int,
    value: float,
    *,
    additive: bool,
) -> None:
    """Apply one effect in place to hours t0..t1-1 of the cells that `scope` selects."""
    index: list[slice | int] = [slice(t0, t1)]
    for dim in dims:
        index.append(DIMENSIONS[dim].index(scope[dim]) if dim in scope else slice(None))
    view = arr[tuple(index)]
    weight = np.ones(t1 - t0)
    if ramp_hours > 0:
        weight = np.minimum(1.0, (np.arange(t1 - t0) + 1) / ramp_hours)
    w = weight.reshape((t1 - t0,) + (1,) * (view.ndim - 1))
    if additive:
        view += w * value
    else:
        view *= 1.0 + w * (value - 1.0)


def _simulate(
    rng: np.random.Generator, params: dict[str, FloatArray], n_hours: int
) -> dict[str, np.ndarray[Any, Any]]:
    ticks = np.arange(n_hours)
    season = HOUR_SHAPE[ticks % 24] * WEEKDAY_SHAPE[(ticks // 24) % 7]
    trend = 1.0 + DAILY_GROWTH * (ticks / 24.0)
    # A shared daily factor per country makes slices move together, as real traffic does.
    day_factor = np.exp(rng.normal(0.0, 0.04, size=(n_hours // 24, C)))[ticks // 24]
    share = (
        COUNTRY_SHARE[:, None, None] * PLATFORM_SHARE[None, :, None] * CHANNEL_SHARE[None, None, :]
    )
    lam = (
        BASE_SESSIONS_PER_HOUR
        * (season * trend)[:, None, None, None]
        * day_factor[:, :, None, None]
        * share[None]
        * params["sessions_mult"]
    )
    lam = lam * rng.gamma(60.0, 1.0 / 60.0, size=lam.shape)  # overdispersion
    real_sessions = rng.poisson(lam)

    # A demand uplift on a category raises its share of orders and the checkout rate.
    weights = CATEGORY_SHARE * params["orders_mult"]  # (T, C, P, H, K)
    uplift = weights.sum(axis=-1)  # (T, C, P, H); 1.0 when nothing is injected
    checkout_p = np.clip(
        CHECKOUT_RATE
        * PLATFORM_CHECKOUT[None, None, :, None]
        * CHANNEL_CHECKOUT[None, None, None, :]
        * params["checkout_rate_mult"]
        * uplift,
        0.0,
        0.9,
    )
    checkouts = rng.binomial(real_sessions, checkout_p)
    # Bots and tracking outages change what is counted, not what customers do.
    sessions = np.rint(real_sessions * params["observed_sessions_mult"]).astype(np.int64)

    reach = rng.binomial(
        checkouts.sum(axis=3), np.clip(COMPLETION_RATE * params["completion_mult"], 0.0, 1.0)
    )
    mix = np.ascontiguousarray(np.broadcast_to(METHOD_MIX[None, :, None, :], (n_hours, C, P, M)))
    attempts = rng.multinomial(reach, mix)
    successes = rng.binomial(
        attempts, np.clip(METHOD_SUCCESS * params["payment_success_mult"], 0.0, 1.0)
    )

    # Every successful payment is an order; spread them over channels, then categories.
    paid = successes.sum(axis=3)
    by_channel = checkouts.astype(np.float64)
    total = by_channel.sum(axis=3, keepdims=True)
    channel_p = np.where(total > 0, by_channel / np.maximum(total, 1.0), 1.0 / H)
    orders_by_channel = rng.multinomial(paid, channel_p)
    orders = rng.multinomial(orders_by_channel, weights / uplift[..., None])

    n = np.maximum(orders, 1)

    def jitter(sd: float) -> FloatArray:
        noise: FloatArray = 1.0 + rng.normal(0.0, sd, size=orders.shape) / np.sqrt(n)
        return noise

    gross = np.clip(orders * AOV * params["price_mult"] * jitter(0.25), 0.0, None)
    rate = np.clip(
        BASE_DISCOUNT + CHANNEL_DISCOUNT_ADD[:, None] + params["discount_rate_add"], 0.0, 0.9
    )
    discount = gross * np.clip(rate * jitter(0.20), 0.0, 0.95)
    cost = np.clip(orders * AOV * COST_SHARE * jitter(0.05), 0.0, None)
    units = orders + rng.poisson(0.3 * orders)

    return {
        "sessions": sessions,
        "checkouts_started": checkouts,
        "attempts": attempts,
        "successes": successes,
        "orders": orders,
        "units": units,
        "gross_revenue": np.round(gross, 2),
        "discount": np.round(discount, 2),
        "cost": np.round(cost, 2),
    }


def _frame(
    config: WorldConfig,
    n_hours: int,
    dims: tuple[str, ...],
    columns: dict[str, np.ndarray[Any, Any]],
) -> pd.DataFrame:
    """Long-format table: one row per hour and cell."""
    idx = np.indices((n_hours, *(SIZES[d] for d in dims))).reshape(len(dims) + 1, -1)
    ts = np.datetime64(config.start, "h") + idx[0].astype("timedelta64[h]")
    data: dict[str, np.ndarray[Any, Any]] = {"ts": ts.astype("datetime64[us]")}
    for i, dim in enumerate(dims):
        data[dim] = np.array(DIMENSIONS[dim])[idx[i + 1]]
    for name, values in columns.items():
        data[name] = values.reshape(-1)
    return pd.DataFrame(data)


def _fact_tables(
    config: WorldConfig, facts: dict[str, np.ndarray[Any, Any]], n_hours: int
) -> dict[str, pd.DataFrame]:
    provider = np.broadcast_to(PROVIDER_TABLE[None, :, None, :], facts["attempts"].shape)
    return {
        "traffic": _frame(
            config,
            n_hours,
            ("country", "platform", "channel"),
            {"sessions": facts["sessions"], "checkouts_started": facts["checkouts_started"]},
        ),
        "payments": _frame(
            config,
            n_hours,
            ("country", "platform", "payment_method"),
            {"provider": provider, "attempts": facts["attempts"], "successes": facts["successes"]},
        ),
        "orders": _frame(
            config,
            n_hours,
            ("country", "platform", "channel", "category"),
            {
                name: facts[name]
                for name in ("orders", "units", "gross_revenue", "discount", "cost")
            },
        ),
    }


def _pick(rng: np.random.Generator, options: Sequence[Any]) -> Any:
    return options[int(rng.integers(len(options)))]


def _noise_change(rng: np.random.Generator) -> tuple[str, str, dict[str, str], str, str]:
    """An unrelated change: (kind, entity, scope, actor, note)."""
    kind = str(rng.choice(NOISE_KINDS, p=NOISE_WEIGHTS))
    if kind == "release":
        platform = _pick(rng, DIMENSIONS["platform"])
        entity = (
            f"{RELEASE_PREFIX[platform]}-"
            f"{rng.integers(1, 9)}.{rng.integers(0, 60)}.{rng.integers(0, 9)}"
        )
        note = f"{entity} rolled out: {_pick(rng, RELEASE_NOTES)}"
        return kind, entity, {"platform": platform}, "deploy-bot", note
    if kind == "coupon":
        code = _pick(rng, COUPONS)
        scope = {"country": _pick(rng, DIMENSIONS["country"])} if rng.random() < 0.4 else {}
        return kind, code, scope, "campaign-sync", f"Coupon {code}: {_pick(rng, COUPON_NOTES)}"
    if kind == "price":
        category = _pick(rng, DIMENSIONS["category"])
        sku = f"{category[:3].upper()}-{rng.integers(10000, 99999)}"
        old = float(rng.uniform(15, 400))
        new = old * float(rng.uniform(0.92, 1.08))
        scope = {"category": category}
        if rng.random() < 0.3:
            scope["country"] = _pick(rng, DIMENSIONS["country"])
        note = f"Price update {sku}: {old:.2f} -> {new:.2f} EUR"
        return kind, sku, scope, "pricing-import", note
    if kind == "provider_status":
        provider = _pick(rng, PROVIDERS)
        note = f"{provider}: scheduled maintenance completed, no customer impact"
        return kind, provider, {}, "status-feed", note
    if kind == "campaign":
        campaign_id, channel, country = _pick(rng, CAMPAIGNS)
        scope = {"channel": channel}
        if country is not None:
            scope["country"] = country
        note = f"Campaign {campaign_id}: {_pick(rng, CAMPAIGN_NOTES)}"
        return kind, campaign_id, scope, "campaign-sync", note
    if kind == "tracking_tag":
        tag = _pick(rng, TAGS)
        return kind, tag, {}, "a.novak", f"{tag}: container published, minor tag update"
    entity = _pick(rng, CONFIGS)
    return "config", entity, {}, "ops-oncall", f"{entity}: configuration updated"


def _changes(
    rng: np.random.Generator, config: WorldConfig, scenarios: Sequence[Scenario], n_hours: int
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """The change log, in time order, and the change id each scenario blames."""
    rows: list[dict[str, Any]] = []
    for _ in range(NOISE_CHANGES_PER_DAY * config.days):
        kind, entity, scope, actor, note = _noise_change(rng)
        ts = config.start + timedelta(minutes=int(rng.integers(0, n_hours * 60)))
        rows.append(
            {"ts": ts, "kind": kind, "entity": entity, "scope": scope, "actor": actor,
             "note": note, "scenario_id": None}
        )  # fmt: skip
    for scenario in scenarios:
        specs: list[tuple[ChangeSpec, str | None]] = []
        if scenario.change is not None:
            specs.append((scenario.change, scenario.id))
        specs += [(decoy, None) for decoy in scenario.decoys]
        for spec, owner in specs:
            rows.append(
                {
                    "ts": scenario.start + timedelta(minutes=spec.offset_minutes),
                    "kind": spec.kind,
                    "entity": spec.entity,
                    "scope": scenario.slice if spec.scope is None else spec.scope,
                    "actor": SCENARIO_ACTOR[spec.kind],
                    "note": spec.note,
                    "scenario_id": owner,
                }
            )
    rows.sort(key=lambda row: (row["ts"], row["kind"], row["entity"]))
    change_ids: dict[str, str] = {}
    for i, row in enumerate(rows):
        row["change_id"] = f"CHG-{i + 1:05d}"
        owner = row.pop("scenario_id")
        if owner is not None:
            change_ids[owner] = row["change_id"]
    return rows, change_ids


def _calendar(scenarios: Sequence[Scenario]) -> list[dict[str, Any]]:
    events = sorted((s for s in scenarios if s.calendar is not None), key=lambda s: (s.start, s.id))
    rows: list[dict[str, Any]] = []
    for i, scenario in enumerate(events):
        assert scenario.calendar is not None
        rows.append(
            {
                "event_id": f"CAL-{i + 1:03d}",
                "start_ts": scenario.start,
                "end_ts": scenario.end,
                "kind": scenario.calendar.kind,
                "scope": scenario.slice,
                "note": scenario.calendar.note,
            }
        )
    return rows


def _digest(
    facts: dict[str, np.ndarray[Any, Any]],
    changes: list[dict[str, Any]],
    calendar: list[dict[str, Any]],
    scenarios: Sequence[Scenario],
) -> str:
    """A short content hash of everything a consumer can read from the world."""
    h = hashlib.sha256()
    for name in sorted(facts):
        h.update(name.encode())
        h.update(np.ascontiguousarray(facts[name]).tobytes())
    dropped = [(s.id, s.drop_table, s.start, s.end) for s in scenarios if s.drop_table]
    h.update(json.dumps([changes, calendar, dropped], default=str, sort_keys=True).encode())
    return h.hexdigest()[:16]


def _write(
    out_path: Path,
    config: WorldConfig,
    n_hours: int,
    tables: dict[str, pd.DataFrame],
    changes: list[dict[str, Any]],
    calendar: list[dict[str, Any]],
    digest: str,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.unlink(missing_ok=True)
    scope_ddl = ", ".join(f"{col} VARCHAR" for col in SCOPE_COLUMNS)
    con = duckdb.connect(str(out_path))
    try:
        for name, frame in tables.items():
            con.register("frame", frame)
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM frame")
            con.unregister("frame")
        con.execute(
            "CREATE TABLE changes (change_id VARCHAR, ts TIMESTAMP, kind VARCHAR, "
            f"entity VARCHAR, {scope_ddl}, actor VARCHAR, note VARCHAR)"
        )
        con.executemany(
            "INSERT INTO changes VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                [row["change_id"], row["ts"], row["kind"], row["entity"]]
                + [row["scope"].get(col) for col in SCOPE_COLUMNS]
                + [row["actor"], row["note"]]
                for row in changes
            ],
        )
        con.execute(
            "CREATE TABLE calendar (event_id VARCHAR, start_ts TIMESTAMP, end_ts TIMESTAMP, "
            f"kind VARCHAR, {scope_ddl}, note VARCHAR)"
        )
        if calendar:
            con.executemany(
                "INSERT INTO calendar VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    [row["event_id"], row["start_ts"], row["end_ts"], row["kind"]]
                    + [row["scope"].get(col) for col in SCOPE_COLUMNS]
                    + [row["note"]]
                    for row in calendar
                ],
            )
        entities = {("provider", provider) for provider in PROVIDERS}
        entities |= {
            (ENTITY_KIND[row["kind"]], row["entity"])
            for row in changes
            if row["kind"] in ENTITY_KIND
        }
        con.execute("CREATE TABLE entities (kind VARCHAR, entity VARCHAR)")
        con.executemany("INSERT INTO entities VALUES (?, ?)", [list(e) for e in sorted(entities)])
        con.execute("CREATE TABLE meta (key VARCHAR, value VARCHAR)")
        con.executemany(
            "INSERT INTO meta VALUES (?, ?)",
            [
                ["start", config.start.isoformat()],
                ["n_hours", str(n_hours)],
                ["seed", str(config.seed)],
                ["world_hash", digest],
            ],
        )
    finally:
        con.close()
