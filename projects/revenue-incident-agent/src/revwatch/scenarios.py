"""Scenario files: what is injected into a world, and the ground truth that comes out."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from revwatch.models import DIMENSIONS, Slice

Cause = Literal[
    "payment_provider_failure",
    "release_regression",
    "coupon_misconfiguration",
    "price_error",
    "bot_traffic",
    "tracking_outage",
    "campaign_change",
    "expected_event",
    "unknown",
]
ChangeKind = Literal[
    "release", "coupon", "price", "provider_status", "campaign", "tracking_tag", "config"
]
CalendarKind = Literal["public_holiday", "planned_promotion", "planned_campaign", "maintenance"]
Action = Literal[
    "failover_payment_provider",
    "rollback_release",
    "disable_coupon",
    "pause_listing",
    "enable_bot_challenge",
    "resume_campaign",
]
Param = Literal[
    "sessions_mult",
    "observed_sessions_mult",
    "checkout_rate_mult",
    "completion_mult",
    "payment_success_mult",
    "discount_rate_add",
    "price_mult",
    "orders_mult",
]
Table = Literal["traffic", "payments", "orders"]

# The dimensions each effect parameter can be scoped by.
PARAM_DIMS: dict[str, tuple[str, ...]] = {
    "sessions_mult": ("country", "platform", "channel"),
    "observed_sessions_mult": ("country", "platform", "channel"),
    "checkout_rate_mult": ("country", "platform", "channel"),
    "completion_mult": ("country", "platform"),
    "payment_success_mult": ("country", "platform", "payment_method"),
    "discount_rate_add": ("country", "platform", "channel", "category"),
    "price_mult": ("country", "platform", "channel", "category"),
    "orders_mult": ("country", "platform", "channel", "category"),
}

# The watched metrics each parameter moves when it goes up: +1 with it, -1 against it.
# This is what the generator does with the parameter (see `world._simulate`), written down
# so that the evaluation can tell an alert the scenario could have caused from one it
# could not.
PARAM_MOVES: dict[str, dict[str, int]] = {
    "sessions_mult": {"sessions": 1},
    # Bots and tracking outages change the sessions counted, not the orders placed.
    "observed_sessions_mult": {"sessions": 1, "checkout_conversion": -1},
    "checkout_rate_mult": {"checkout_conversion": 1},
    "completion_mult": {"checkout_conversion": 1},
    "payment_success_mult": {"payment_success_rate": 1, "checkout_conversion": 1},
    "discount_rate_add": {"discount_rate": 1, "gross_margin_rate": -1},
    "price_mult": {"gross_margin_rate": 1},
    "orders_mult": {"checkout_conversion": 1},
}

# What each mitigation acts on. The executor will refuse anything else.
TARGET_KEYS: dict[str, tuple[str, ...]] = {
    "failover_payment_provider": ("provider", "country"),
    "rollback_release": ("release_id",),
    "disable_coupon": ("code",),
    "pause_listing": ("sku",),
    "enable_bot_challenge": ("country", "channel"),
    "resume_campaign": ("campaign_id",),
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Effect(_Strict):
    """One change to a generator parameter. `scope` defaults to the scenario's slice."""

    param: Param
    value: float
    scope: dict[str, str] | None = None


class ChangeSpec(_Strict):
    """A change-log entry written `offset_minutes` from the scenario's start."""

    kind: ChangeKind
    entity: str
    note: str
    offset_minutes: int = -15
    scope: dict[str, str] | None = None


class CalendarSpec(_Strict):
    kind: CalendarKind
    note: str


class Mitigation(_Strict):
    action: Action
    target: dict[str, str]


class Scenario(_Strict):
    """An injected event. It covers hours `start <= hour < end`."""

    id: str
    kind: Literal["incident", "benign"]
    cause: Cause
    start: datetime
    end: datetime
    slice: dict[str, str] = Field(default_factory=dict)
    labelled: bool = True
    effects: list[Effect] = Field(default_factory=list)
    ramp_hours: int = 0
    change: ChangeSpec | None = None
    decoys: list[ChangeSpec] = Field(default_factory=list)
    calendar: CalendarSpec | None = None
    mitigation: Mitigation | None = None
    drop_table: Table | None = None

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.start.tzinfo is not None or self.end.tzinfo is not None:
            raise ValueError(f"{self.id}: times must not carry a time zone; UTC is assumed")
        if self.end < self.start:
            raise ValueError(f"{self.id}: end is before start")
        if self.end == self.start:
            raise ValueError(f"{self.id}: covers no hours; end must be after start")
        if any(t.minute or t.second or t.microsecond for t in (self.start, self.end)):
            raise ValueError(f"{self.id}: start and end must be on the hour; the world is hourly")
        if self.ramp_hours < 0:
            raise ValueError(f"{self.id}: ramp_hours cannot be negative")
        if self.kind == "incident" and not self.effects:
            raise ValueError(f"{self.id}: an incident needs at least one effect")
        if self.drop_table is not None and self.slice:
            raise ValueError(
                f"{self.id}: drop_table drops the whole table for these hours; remove the slice"
            )
        scopes: list[tuple[str, dict[str, str]]] = [("", self.slice)]
        scopes += [("", self.scope_of(effect)) for effect in self.effects]
        if self.change is not None:
            scopes.append(("change: ", self.change.scope or {}))
        scopes += [("decoy: ", decoy.scope or {}) for decoy in self.decoys]
        if self.mitigation is not None:
            wanted, got = TARGET_KEYS[self.mitigation.action], tuple(self.mitigation.target)
            if sorted(wanted) != sorted(got):
                raise ValueError(
                    f"{self.id}: {self.mitigation.action} needs target {', '.join(wanted)}, "
                    f"got {', '.join(got) or 'nothing'}"
                )
            # Targets that are places (a country, a channel) must be real ones.
            places = {k: v for k, v in self.mitigation.target.items() if k in DIMENSIONS}
            scopes.append(("mitigation target: ", places))
        for what, scope in scopes:
            try:
                Slice.from_dict(scope)
            except ValueError as exc:
                raise ValueError(f"{self.id}: {what}{exc}") from exc
        for effect in self.effects:
            extra = sorted(set(self.scope_of(effect)) - set(PARAM_DIMS[effect.param]))
            if extra:
                raise ValueError(f"{self.id}: {effect.param} cannot be scoped by {extra}")
        return self

    def scope_of(self, effect: Effect) -> dict[str, str]:
        return self.slice if effect.scope is None else effect.scope

    def moves(self) -> dict[str, int]:
        """The watched metrics this scenario moves: +1 up, -1 down, 0 when effects disagree."""
        found: dict[str, int] = {}
        for effect in self.effects:
            neutral = 0.0 if effect.param.endswith("_add") else 1.0
            if effect.value == neutral:
                continue
            up = 1 if effect.value > neutral else -1
            for metric, sign in PARAM_MOVES[effect.param].items():
                found[metric] = up * sign if found.get(metric, up * sign) == up * sign else 0
        return found


class Label(_Strict):
    """Ground truth for one scenario, written next to the generated world."""

    scenario_id: str
    kind: Literal["incident", "benign"]
    cause: Cause
    start: datetime
    end: datetime
    slice: dict[str, str]
    labelled: bool
    change_id: str | None
    mitigation: Mitigation | None
    drop_table: Table | None = None  # set when the scenario is late data in that table
    # Watched metric -> the direction the scenario moves it (0: either). Empty: not stated.
    moves: dict[str, int] = Field(default_factory=dict)

    @property
    def label_slice(self) -> Slice:
        return Slice.from_dict(self.slice)


def load_scenarios(path: Path) -> list[Scenario]:
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"{path}: not valid YAML: {exc}") from exc
    if not isinstance(raw, list):
        raise ValueError(f"{path}: expected a list of scenarios")
    scenarios: list[Scenario] = []
    for position, item in enumerate(raw, start=1):
        try:
            scenarios.append(Scenario.model_validate(item))
        except ValidationError as exc:
            name = item.get("id", f"#{position}") if isinstance(item, dict) else f"#{position}"
            raise ValueError(f"{path}: scenario {name}: {exc}") from exc
    seen: set[str] = set()
    for item in scenarios:
        if item.id in seen:
            raise ValueError(f"{path}: duplicate scenario id: {item.id}")
        seen.add(item.id)
    return scenarios


def save_labels(labels: Sequence[Label], path: Path) -> None:
    path.write_text(json.dumps([label.model_dump(mode="json") for label in labels], indent=2))


def load_labels(path: Path) -> list[Label]:
    return [Label.model_validate(item) for item in json.loads(path.read_text())]
