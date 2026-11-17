"""The scoreboard: one JSON file and one static page that shows it.

The page has no build step and loads nothing from the network. The numbers are
embedded when the page is written, so it opens from disk or from any static host.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import Settings
from .evals import Score
from .report import collect, config_name

TEMPLATE = Path(__file__).with_name("scoreboard.html")


def _row(s: Score, meta: dict) -> dict:
    every, sure = s.all_labels(), s.all_labels(sure=True)
    return {
        "config": s.config,
        "label": config_name(s, meta),
        "version": s.system_version,
        "issues": s.issues,
        "labels_proposed": every.proposed,
        "labels_right": every.right,
        "labels_present": every.present,
        "labelled": s.labelled,
        "primary_right": s.primary_right,
        "applied_alone": sure.proposed,
        "applied_alone_wrong": sure.wrong,
        "applied_alone_on_unlabelled": s.sure_on_unlabelled,
        "duplicates": s.duplicates,
        "flagged": s.flagged,
        "flagged_right": s.flagged_right,
        "false_duplicates": s.false_duplicates,
        "false_duplicates_sure": s.false_duplicates_sure,
        "alone": s.alone,
        "alone_clean": s.alone_clean,
        "person": s.person,
        "model_answers": s.model_answers,
        "cost_per_1000": round(1000 * s.cost_usd / s.issues, 4) if s.issues else 0.0,
        "seen_first": s.seen_first,
    }


def build(settings: Settings, *, now: datetime | None = None, health: dict | None = None) -> dict:
    """Every number on the scoreboard, from the committed logs."""
    now = now or datetime.now(UTC)
    data = collect(settings)
    meta = data["meta"]
    live = data["live_predictions"]
    runs = data["live_runs"]
    cost_by_day: dict[str, float] = defaultdict(float)
    for prediction in live:
        cost_by_day[prediction.written_at.astimezone(UTC).date().isoformat()] += prediction.cost_usd
    today = now.date().isoformat()
    month_ago = (now.date() - timedelta(days=30)).isoformat()
    answered = {p.number for p in live}
    early = {p.number for p in live if not (p.seen_labels or p.seen_closed)}
    scored = max((s.issues for s in data["live"]), default=0)
    return {
        "project": "issue-triage-desk",
        "repo": settings.repo,
        "generated_at": now.isoformat(timespec="seconds"),
        "truth_after_days": settings.truth_after_days,
        "live": {
            "since": min(p.written_at for p in live).date().isoformat() if live else None,
            "runs": len(runs),
            "last_run_at": runs[-1]["at"] if runs else None,
            "issues_answered": len(answered),
            "answered_before_label": len(early),
            "outcomes_read": len(data["live_outcomes"]),
            "scored": scored,
            "waiting": len(early - set(data["live_outcomes"])),
            "scores": [_row(s, meta) for s in data["live"] if s.issues],
        },
        "backtest": {
            "cut": settings.cut.isoformat(),
            "held_out_start": settings.held_out_start.isoformat(),
            "test_end": settings.test_end.isoformat(),
            "stored_issues": meta.get("stored_issues"),
            "training_issues": meta.get("training_issues"),
            "held_out": [_row(s, meta) for s in data["held_out"]],
            "development": [_row(s, meta) for s in data["development"]],
            "held_out_truth": data["held_out_truth"],
            "development_truth": data["development_truth"],
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
