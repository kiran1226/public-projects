"""The scoreboard: one JSON file and one static page that shows it.

The page has no build step and loads nothing from the network. The numbers are
embedded when the page is written, so it opens from disk, from GitHub Pages or from
any static host.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .evals import RANKER_LABEL, RANKER_ORDER, Score
from .report import collect

TEMPLATE = Path(__file__).with_name("scoreboard.html")


def _score_row(score: Score) -> dict:
    return {
        "ranker": score.ranker,
        "label": RANKER_LABEL.get(score.ranker, score.ranker),
        "version": score.system_version,
        "outcomes": score.outcomes,
        "top1": score.found[1],
        "top3": score.found[3],
        "top5": score.found[5],
        "top15": score.found[15],
        "median_place": score.median_rank,
        "never_listed": score.not_listed,
        "removed_by_rule": score.removed_by_rule,
        "skipped_by_model": score.skipped_by_model if score.assessments else None,
        "mean_list_length": round(score.mean_list_length, 1),
    }


def _rows(scores: list[Score]) -> list[dict]:
    position = {name: i for i, name in enumerate(RANKER_ORDER)}
    ordered = sorted(scores, key=lambda s: (position.get(s.ranker, 99), s.system_version))
    return [_score_row(s) for s in ordered if s.outcomes]


def build(settings: Settings, *, now: datetime | None = None, health: dict | None = None) -> dict:
    """Every number on the scoreboard, from the committed logs."""
    now = now or datetime.now(UTC)
    data = collect(settings)
    live = data["live_lists"]
    live_days = sorted({s.day for s in live})
    cost_by_day: dict[str, float] = defaultdict(float)
    for shortlist in live:
        cost_by_day[shortlist.written_at.date().isoformat()] += shortlist.cost_usd
    today = now.date().isoformat()
    month_ago = (now.date() - timedelta(days=30)).isoformat()
    scored = sum(s.outcomes for s in data["live"])
    client_lists = [s for s in live if s.profile_id == "client"]
    return {
        "project": "tender-radar",
        "generated_at": now.isoformat(timespec="seconds"),
        "live": {
            "since": min(s.written_at for s in live).date().isoformat() if live else None,
            "first_day": live_days[0].isoformat() if live_days else None,
            "last_day": live_days[-1].isoformat() if live_days else None,
            "last_written_at": max((s.written_at for s in live), default=None)
            and max(s.written_at for s in live).isoformat(timespec="seconds"),
            "days": len(live_days),
            "lists": len(live),
            "tenders_listed": len({e.publication_number for s in live for e in s.entries}),
            "client_lists": len(client_lists),
            "client_assessments": sum(
                1 for s in client_lists for e in s.entries if e.assessment is not None
            ),
            "outcomes_arrived": len(data["live_outcomes"]),
            "outcomes_scored": scored,
            "written_too_late": sum(s.written_too_late for s in data["live"]),
            "scores": _rows(data["live"]),
        },
        "backtest": {
            "meta": data["meta"],
            "held_out_start": data["held_out_start"].isoformat(),
            "outcomes": len(data["backtest_outcomes"]),
            "development": _rows(data["development"]),
            "held_out": _rows(data["held_out"]),
            "model_sample": _rows(data["model_sample"]),
            "model_sample_development": _rows(data["model_sample_development"]),
            "model_sample_held_out": _rows(data["model_sample_held_out"]),
            "model": data["model"],
        },
        "cost": {
            "cap_usd_per_day": settings.daily_cap_usd,
            "today_usd": round(cost_by_day.get(today, 0.0), 4),
            "last_30_days_usd": round(
                sum(cost for day, cost in cost_by_day.items() if day >= month_ago), 4
            ),
            "live_total_usd": round(sum(cost_by_day.values()), 4),
        },
        "health": health,
    }


def write(settings: Settings, *, now: datetime | None = None, health: dict | None = None) -> Path:
    """Write scoreboard.json and index.html. Returns the page's path."""
    board = build(settings, now=now, health=health)
    out = settings.scoreboard_dir
    out.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(board, indent=1, ensure_ascii=False)
    (out / "scoreboard.json").write_text(payload + "\n", encoding="utf-8")
    # "</" inside a script element would end it early; JSON allows the escaped form.
    embedded = payload.replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*SCOREBOARD_DATA*/null", embedded)
    (out / "index.html").write_text(page, encoding="utf-8")
    return out / "index.html"
