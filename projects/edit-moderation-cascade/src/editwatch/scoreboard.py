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
from .evals import Score, percentile
from .report import collect, name_of

TEMPLATE = Path(__file__).with_name("scoreboard.html")


def _row(s: Score, meta: dict) -> dict:
    return {
        "config": s.config,
        "label": name_of(s, meta),
        "lead": s.config.startswith("cascade"),
        "edits": s.edits,
        "reverted": s.reverted,
        "top2": s.hits[0.02],
        "top5": s.hits[0.05],
        "top10": s.hits[0.10],
        "top20": s.hits[0.20],
        "bottom_half": s.bottom_half,
        "model_read": s.model_read,
        "cost_per_1000": round(s.cost_per_1000, 4),
    }


def build(settings: Settings, *, now: datetime | None = None, health: dict | None = None) -> dict:
    """Every number on the scoreboard, from the committed logs."""
    from .report import _ordered

    now = now or datetime.now(UTC)
    data = collect(settings)
    meta = data["meta"]
    live = data["live_predictions"]
    runs = data["live_runs"]
    cost_by_day: dict[str, float] = defaultdict(float)
    for p in live:
        cost_by_day[p.written_at.astimezone(UTC).date().isoformat()] += p.cost_usd
    today = now.date().isoformat()
    month_ago = (now.date() - timedelta(days=30)).isoformat()
    model_lat = [p.latency_s for p in live if p.verdict.source == "model" and p.latency_s]
    tier1_lat = [p.latency_s for p in live if p.verdict.source != "model" and p.latency_s]
    skipped = sum(1 for p in live if p.verdict.skipped in ("queue_full", "budget"))
    outcomes = data["live_outcomes"]
    return {
        "project": "edit-moderation-cascade",
        "wiki": settings.wiki_host,
        "generated_at": now.isoformat(timespec="seconds"),
        "truth_after_hours": settings.truth_after_hours,
        "latency_target_s": settings.latency_target_s,
        "live": {
            "since": min(p.written_at for p in live).date().isoformat() if live else None,
            "runs": len(runs),
            "last_run_at": runs[-1]["at"] if runs else None,
            "edits": len(live),
            "read_by_model": len(model_lat),
            "skipped": skipped,
            "model_p50_s": percentile(model_lat, 0.50),
            "model_p95_s": percentile(model_lat, 0.95),
            "tier1_p95_s": percentile(tier1_lat, 0.95),
            # The figures of the last run on their own: a run after a fix, or on a slow
            # day, should not be averaged away by the runs before it.
            "last_run": {
                key: runs[-1].get(key) for key in ("model_p50_s", "model_p95_s", "tier1_p95_s")
            }
            if runs
            else None,
            "scored": len(outcomes),
            "waiting": len({p.rev_id for p in live} - set(outcomes)),
            "scores": [_row(s, meta) for s in _ordered(data["live"], meta)],
        },
        "backtest": {
            "held_out": [_row(s, meta) for s in data.get("held_out", [])],
            "development": [_row(s, meta) for s in data.get("development", [])],
            "held_out_n": data.get("held_out_n", 0),
            "held_out_reverted": data.get("held_out_reverted", 0),
            "development_n": data.get("development_n", 0),
            "development_reverted": data.get("development_reverted", 0),
            "windows": data["tier1"].get("windows", {}),
        },
        "loadtest": data["loadtest"] or None,
        "cost": {
            "cap_usd_per_day": settings.daily_cap_usd,
            "today_usd": round(cost_by_day.get(today, 0.0), 4),
            "last_30_days_usd": round(
                sum(c for day, c in cost_by_day.items() if day >= month_ago), 4
            ),
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
