"""Settings in one place: what is fetched, how the time split is cut, what a run may spend."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Category (CPV) divisions the radar watches. The whole feed is about 3,100 notices a
# day; these three are what a laboratory and diagnostics supplier would follow.
SCOPE_DIVISIONS: tuple[str, ...] = ("24", "33", "38")
DIVISION_LABELS = {
    "24": "Chemical products",
    "33": "Medical equipment, pharmaceuticals and personal care products",
    "38": "Laboratory, optical and precision equipment",
}

# The time split of the backtest. Profiles see awards published before CUT_DATE only.
PROFILE_START = date(2025, 1, 1)
CUT_DATE = date(2026, 4, 1)
TEST_END = date(2026, 6, 30)
# The last month of the test window is held back from tuning and scored once.
HELD_OUT_START = date(2026, 6, 1)

# Approximate euro values of other currencies, used only by the value-band rule.
# Rounded figures: the rule compares orders of magnitude, not prices.
EUR_PER_UNIT = {
    "EUR": 1.0,
    "PLN": 0.23,
    "CZK": 0.04,
    "HUF": 0.0025,
    "RON": 0.20,
    "SEK": 0.09,
    "DKK": 0.134,
    "NOK": 0.085,
    "CHF": 1.05,
    "GBP": 1.15,
    "ISK": 0.0068,
    "BGN": 0.51,
}


@dataclass(frozen=True)
class Settings:
    db_path: Path = PROJECT_ROOT / "var" / "tenderradar.db"
    embedding_cache: Path = PROJECT_ROOT / "var" / "embeddings.sqlite"
    log_dir: Path = PROJECT_ROOT / "data" / "log"
    cassette_dir: Path = PROJECT_ROOT / "data" / "cassettes"
    profiles_dir: Path = PROJECT_ROOT / "data" / "profiles"
    client_profile: Path = PROJECT_ROOT / "data" / "client_profile.yaml"
    fixture_dir: Path = PROJECT_ROOT / "data" / "fixture"
    scoreboard_dir: Path = PROJECT_ROOT / "var" / "scoreboard"
    divisions: tuple[str, ...] = SCOPE_DIVISIONS
    list_size: int = 15
    # How many of the ranked candidates the model reads per profile per day.
    assess_top: int = 10
    daily_cap_usd: float = 0.50
    # Seconds between requests to the feed. A burst of ten requests in a few seconds
    # was refused on 2026-11-17; this spacing was not.
    feed_pause_s: float = 1.2
    user_agent: str = "tender-radar/0.1"
    extra: dict = field(default_factory=dict)


def load_settings() -> Settings:
    """Defaults, with the few values an operator changes read from the environment."""
    root = Path(os.environ["TENDERRADAR_HOME"]) if os.environ.get("TENDERRADAR_HOME") else None
    kwargs: dict = {}
    if root is not None:
        kwargs.update(
            db_path=root / "var" / "tenderradar.db",
            embedding_cache=root / "var" / "embeddings.sqlite",
            log_dir=root / "data" / "log",
            scoreboard_dir=root / "var" / "scoreboard",
        )
    if os.environ.get("TENDERRADAR_DAILY_CAP_USD"):
        kwargs["daily_cap_usd"] = float(os.environ["TENDERRADAR_DAILY_CAP_USD"])
    if os.environ.get("TENDERRADAR_USER_AGENT"):
        kwargs["user_agent"] = os.environ["TENDERRADAR_USER_AGENT"]
    return Settings(**kwargs)
