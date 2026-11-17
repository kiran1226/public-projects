"""Settings in one place: which tracker is served, how the time split is cut, what a run may spend.

Nothing here is specific to one repository. The tracker, its label groups and the
keywords of the rules baseline live in `triagedesk.yaml`, which `triagedesk init`
writes for any public repository.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_NAME = "triagedesk.yaml"


@dataclass(frozen=True)
class GroupConfig:
    """One kind of label the desk proposes, for example the area or the platform."""

    name: str
    prefix: str
    # A label seen on fewer training issues than this cannot be learned or scored.
    min_issues: int = 30
    # One sentence the model is given on when labels of this group apply.
    hint: str = ""


@dataclass(frozen=True)
class Settings:
    repo: str = "godotengine/godot"
    groups: tuple[GroupConfig, ...] = (
        GroupConfig("area", "topic:"),
        GroupConfig("platform", "platform:"),
    )
    # Extra words for the rules baseline, per label. The label's own name is always one.
    keywords: dict[str, tuple[str, ...]] = field(default_factory=dict)

    # The time split of the backtest. Training and the search index see issues opened
    # before `cut` only; later issues enter the index as the replay reaches them.
    history_start: date = date(2022, 1, 1)
    cut: date = date(2026, 5, 1)
    test_end: date = date(2026, 10, 31)
    # The last part of the test window is held back from tuning and scored once.
    held_out_start: date = date(2026, 9, 1)
    # The live desk is trained on issues opened before this day. Moving it forward
    # retrains the classifier and makes a new system version. None: same as `cut`.
    live_cut: date | None = None
    # An issue's outcome is read this many days after it was opened.
    truth_after_days: int = 14

    # Earlier issues retrieved per new issue, and how many of them the model reads.
    candidates: int = 20
    model_candidates: int = 8
    # Rank fusion: an issue scores 1 / (fusion_k + rank) in each search that found it.
    # A small value trusts each search's first few results more.
    fusion_k: int = 10
    # How earlier issues are searched: "fused", "vector" or "words". Chosen on the
    # development months; the decision log has the numbers.
    retrieval: str = "fused"
    # The no-model duplicate call: similarity of the first candidate at which the
    # classifier configuration says "duplicate", sure and unsure.
    duplicate_confident: float = 0.95
    duplicate_propose: float = 0.90
    daily_cap_usd: float = 0.50
    # Repositories the desk may write to with --apply. Empty: it can write nowhere.
    apply_allow: tuple[str, ...] = ()
    user_agent: str = "issue-triage-desk/0.1"

    home: Path = PROJECT_ROOT

    @property
    def db_path(self) -> Path:
        return self.home / "var" / "triagedesk.db"

    @property
    def log_dir(self) -> Path:
        return self.home / "data" / "log"

    @property
    def cassette_dir(self) -> Path:
        return self.home / "data" / "cassettes"

    @property
    def fixture_dir(self) -> Path:
        return self.home / "data" / "fixture"

    @property
    def scoreboard_dir(self) -> Path:
        return self.home / "var" / "scoreboard"

    @property
    def local_cassette_dir(self) -> Path:
        """Recordings made on real issues. They can quote issue text, so they stay local."""
        return self.home / "var" / "cassettes"

    def group_of(self, label: str) -> str | None:
        for group in self.groups:
            if label.startswith(group.prefix):
                return group.name
        return None

    def truth_due(self, created_at: datetime) -> datetime:
        return created_at + timedelta(days=self.truth_after_days)


def _as_date(value) -> date:
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def settings_from_mapping(raw: dict, base: Settings | None = None) -> Settings:
    """Read the YAML form. Unknown keys are an error: a typo must not pass silently."""
    base = base or Settings()
    known = {
        "repo", "groups", "keywords", "history_start", "cut", "test_end", "held_out_start",
        "truth_after_days", "candidates", "model_candidates", "daily_cap_usd", "apply_allow",
        "user_agent", "retrieval", "duplicate_confident", "duplicate_propose", "live_cut",
        "fusion_k",
    }  # fmt: skip
    unknown = sorted(set(raw) - known)
    if unknown:
        raise ValueError(f"Unknown setting(s) in {CONFIG_NAME}: {', '.join(unknown)}")
    changes: dict = {}
    for key in ("repo", "truth_after_days", "candidates", "model_candidates", "user_agent",
                "retrieval", "fusion_k"):  # fmt: skip
        if key in raw:
            changes[key] = raw[key]
    for key in ("duplicate_confident", "duplicate_propose"):
        if key in raw:
            changes[key] = float(raw[key])
    if "daily_cap_usd" in raw:
        changes["daily_cap_usd"] = float(raw["daily_cap_usd"])
    for key in ("history_start", "cut", "test_end", "held_out_start"):
        if key in raw:
            changes[key] = _as_date(raw[key])
    if raw.get("live_cut"):
        changes["live_cut"] = _as_date(raw["live_cut"])
    if "groups" in raw:
        changes["groups"] = tuple(
            GroupConfig(
                name, spec["prefix"], int(spec.get("min_issues", 30)), str(spec.get("hint", ""))
            )
            for name, spec in raw["groups"].items()
        )
    if "keywords" in raw:
        changes["keywords"] = {
            label: tuple(str(w).lower() for w in words)
            for label, words in (raw["keywords"] or {}).items()
        }
    if "apply_allow" in raw:
        changes["apply_allow"] = tuple(raw["apply_allow"] or ())
    settings = replace(base, **changes)
    if not (settings.cut <= settings.held_out_start <= settings.test_end):
        raise ValueError("Dates must satisfy cut <= held_out_start <= test_end.")
    if settings.history_start > settings.cut:
        raise ValueError("history_start must not be after cut.")
    if settings.live_cut is not None and settings.live_cut < settings.cut:
        raise ValueError("live_cut must not be before cut.")
    if settings.retrieval not in ("fused", "vector", "words"):
        raise ValueError('retrieval must be "fused", "vector" or "words".')
    if settings.duplicate_propose > settings.duplicate_confident:
        raise ValueError("duplicate_propose must not be above duplicate_confident.")
    if "/" not in settings.repo:
        raise ValueError("repo must be written as owner/name.")
    return settings


def to_mapping(settings: Settings) -> dict:
    return {
        "repo": settings.repo,
        "groups": {
            g.name: {"prefix": g.prefix, "min_issues": g.min_issues, "hint": g.hint}
            for g in settings.groups
        },
        "keywords": {label: list(words) for label, words in sorted(settings.keywords.items())},
        "history_start": settings.history_start.isoformat(),
        "cut": settings.cut.isoformat(),
        "test_end": settings.test_end.isoformat(),
        "held_out_start": settings.held_out_start.isoformat(),
        "live_cut": settings.live_cut.isoformat() if settings.live_cut else None,
        "truth_after_days": settings.truth_after_days,
        "candidates": settings.candidates,
        "model_candidates": settings.model_candidates,
        "retrieval": settings.retrieval,
        "fusion_k": settings.fusion_k,
        "duplicate_confident": settings.duplicate_confident,
        "duplicate_propose": settings.duplicate_propose,
        "daily_cap_usd": settings.daily_cap_usd,
        "apply_allow": list(settings.apply_allow),
    }


def load_settings(home: Path | None = None) -> Settings:
    """The YAML file in the project's home, with the few operator values from the environment."""
    if home is None:
        home = Path(os.environ.get("TRIAGEDESK_HOME") or PROJECT_ROOT)
    settings = Settings(home=home)
    path = home / CONFIG_NAME
    if path.exists():
        settings = settings_from_mapping(yaml.safe_load(path.read_text("utf-8")) or {}, settings)
    if os.environ.get("TRIAGEDESK_DAILY_CAP_USD"):
        settings = replace(settings, daily_cap_usd=float(os.environ["TRIAGEDESK_DAILY_CAP_USD"]))
    if os.environ.get("TRIAGEDESK_USER_AGENT"):
        settings = replace(settings, user_agent=os.environ["TRIAGEDESK_USER_AGENT"])
    return settings


def day_start(day: date) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC)
