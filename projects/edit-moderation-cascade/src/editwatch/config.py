"""Settings in one place: which wiki is watched, how the time split is cut, what a run may spend."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# What the model may name as the kind of problem. Fixed: the page writes the sentence.
CATEGORIES = (
    "vandalism",
    "spam_or_promotion",
    "false_or_unsourced_claim",
    "test_or_accident",
    "content_removal",
    "other",
    "none",
)


def utc(year: int, month: int, day: int, hour: int = 0) -> datetime:
    return datetime(year, month, day, hour, tzinfo=UTC)


@dataclass(frozen=True)
class Settings:
    # The stream is every wiki; the desk keeps article edits by people on this one.
    wiki_host: str = "en.wikipedia.org"
    wiki_db: str = "enwiki"
    namespace: int = 0

    # The backtest. Tier 1 trains on edits made before `cut`. The test week runs from
    # `cut` to `test_end`; its last two days, from `held_out_start`, are scored once.
    # `test_end` is 48 hours before the labels were read, so every edit has an outcome.
    history_start: datetime = utc(2026, 10, 25)
    cut: datetime = utc(2026, 11, 8, 20)
    held_out_start: datetime = utc(2026, 11, 13, 20)
    test_end: datetime = utc(2026, 11, 15, 20)
    # History is sampled: a slice of `slice_edits` edits every `slice_hours` hours.
    slice_hours: int = 2
    slice_edits: int = 500
    # An edit counts as bad if it carries the tag `mw-reverted` this long after it was made.
    truth_after_hours: int = 48

    # The share of the stream, by tier-1 score, that the model reads.
    model_share: float = 0.20
    # The longest change the model is shown, in characters.
    change_chars: int = 1800
    # Between the tiers: how many edits may wait, and how many model calls run at once.
    queue_bound: int = 50
    workers: int = 4
    # Seconds from an edit's event to its place in the queue, at the 95th percentile.
    latency_target_s: float = 15.0
    daily_cap_usd: float = 1.00
    # The model the live run asks. Chosen from the evaluation; the decision log says why.
    live_model: str = "claude-haiku-5-5"
    # The backtest's model sample: this many edits from the development days and as
    # many from the held-out days, drawn at random with these seeds.
    sample_size: int = 1500
    sample_seeds: tuple[int, int] = (11, 13)

    # Seconds between requests to the wiki API. A burst of eleven requests in a row was
    # refused on 2026-11-17; one every five seconds was not.
    wiki_pause_s: float = 5.0
    risk_pause_s: float = 1.0
    user_agent: str = "editwatch/0.1 (edit moderation cascade; open-source portfolio project)"

    home: Path = PROJECT_ROOT

    @property
    def db_path(self) -> Path:
        return self.home / "var" / "editwatch.db"

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
    def recordings_dir(self) -> Path:
        return self.home / "var" / "recordings"

    def truth_due(self, made_at: datetime) -> datetime:
        return made_at + timedelta(hours=self.truth_after_hours)


def load_settings(home: Path | None = None) -> Settings:
    """Defaults, with the few values an operator changes read from the environment."""
    settings = Settings(home=home or Path(os.environ.get("EDITWATCH_HOME") or PROJECT_ROOT))
    if os.environ.get("EDITWATCH_DAILY_CAP_USD"):
        settings = replace(settings, daily_cap_usd=float(os.environ["EDITWATCH_DAILY_CAP_USD"]))
    if os.environ.get("EDITWATCH_USER_AGENT"):
        settings = replace(settings, user_agent=os.environ["EDITWATCH_USER_AGENT"])
    if os.environ.get("EDITWATCH_MODEL_SHARE"):
        settings = replace(settings, model_share=float(os.environ["EDITWATCH_MODEL_SHARE"]))
    return settings
