"""The evaluation report, written by a command and never by hand.

Everything here is computed from the committed logs: the lists the system wrote and
the outcomes that arrived later. No feed, no database and no model is needed.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path

from .config import HELD_OUT_START, Settings
from .evals import Score, score, table, wilson_interval
from .log import PredictionLog
from .models import Outcome, Shortlist
from .truth import OutcomeLog

BACKTEST_LOG = "backtest.jsonl.gz"
BACKTEST_OUTCOMES = "outcomes-backtest.jsonl"
BACKTEST_META = "backtest-meta.json"
LIVE_LOG = "live.jsonl"
LIVE_OUTCOMES = "outcomes-live.jsonl"
LIVE_DAYS = "live-days.jsonl"
MODEL_RANKER = "fused+model"


def load_meta(settings: Settings) -> dict:
    path = settings.log_dir / BACKTEST_META
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _by_ranker(scores: list[Score]) -> dict[str, Score]:
    return {s.ranker: s for s in scores}


def _headline(scores: list[Score], k: int = 3) -> str:
    """One sentence comparing the fused ranker with the alert on the same outcomes."""
    by = _by_ranker(scores)
    if "alert" not in by or "fused" not in by or not by["fused"].outcomes:
        return ""
    alert, fused = by["alert"], by["fused"]
    low, high = wilson_interval(fused.found[k], fused.outcomes)
    return (
        f"Of {fused.outcomes} tenders that panel suppliers went on to win, the fused ranker "
        f"had {fused.found[k]} in the first {k} places of that day's list "
        f"({fused.share(k):.0%}, 95% interval {low:.0%} to {high:.0%}). The category alert "
        f"had {alert.found[k]} of {alert.outcomes} ({alert.share(k):.0%}). The fused ranker never "
        f"listed {fused.not_listed}; the alert never listed {alert.not_listed}."
    )


def model_facts(shortlists: list[Shortlist]) -> dict:
    """What the model step did, read from the lists it wrote."""
    assessments = [
        e.assessment
        for s in shortlists
        if s.ranker == MODEL_RANKER
        for e in s.entries
        if e.assessment is not None
    ]
    return {
        "lists": sum(1 for s in shortlists if s.ranker == MODEL_RANKER),
        "assessments": len(assessments),
        "decisions": dict(Counter(a.decision for a in assessments)),
        "downgraded": sum(1 for a in assessments if a.downgraded),
        "conditions_kept": sum(len(a.conditions) for a in assessments),
        "conditions_dropped": sum(a.dropped_conditions for a in assessments),
        "cost_usd": round(sum(a.cost_usd for a in assessments), 4),
    }


def collect(settings: Settings) -> dict:
    """Every number the report and the scoreboard show."""
    backtest = list(PredictionLog(settings.log_dir / BACKTEST_LOG).read())
    backtest_outcomes = OutcomeLog(settings.log_dir / BACKTEST_OUTCOMES).read()
    live = list(PredictionLog(settings.log_dir / LIVE_LOG).read())
    live_outcomes = OutcomeLog(settings.log_dir / LIVE_OUTCOMES).read()
    meta = load_meta(settings)
    held_out_start = date.fromisoformat(meta.get("held_out_start", HELD_OUT_START.isoformat()))
    plain = [s for s in backtest if s.ranker != MODEL_RANKER]
    return {
        "meta": meta,
        "held_out_start": held_out_start,
        "development": score(plain, backtest_outcomes, end=_day_before(held_out_start)),
        "held_out": score(plain, backtest_outcomes, start=held_out_start),
        "model_sample": score(backtest, backtest_outcomes, only_lists_of=MODEL_RANKER),
        "model_sample_development": score(
            backtest,
            backtest_outcomes,
            only_lists_of=MODEL_RANKER,
            end=_day_before(held_out_start),
        ),
        "model_sample_held_out": score(
            backtest, backtest_outcomes, only_lists_of=MODEL_RANKER, start=held_out_start
        ),
        "model": model_facts(backtest),
        "live": score(live, live_outcomes),
        "live_lists": live,
        "live_outcomes": live_outcomes,
        "backtest_outcomes": backtest_outcomes,
    }


def _day_before(day: date) -> date:
    return date.fromordinal(day.toordinal() - 1)


def _rule_counts(outcomes: list[Outcome]) -> Counter:
    return Counter(rule for o in outcomes for rule in o.filter_hits[:1])


def render(settings: Settings) -> str:
    data = collect(settings)
    meta, model = data["meta"], data["model"]
    lines = [
        "# Evaluation report",
        "",
        "Written by `tenderradar eval`. Do not edit by hand: change the system, replay, "
        "and run the command again.",
        "",
        "## What was run",
        "",
    ]
    if meta:
        lines += [
            f"- Feed: TED competition notices and award notices in category divisions "
            f"{', '.join(meta.get('divisions', []))}.",
            f"- History the profiles were built from: {meta.get('awards_before_cut', 'n/a')} "
            f"awards published {meta.get('profile_start')} to the day before {meta.get('cut')}.",
            f"- Test window: tenders first published {meta.get('cut')} to {meta.get('test_end')}: "
            f"{meta.get('tenders')} tenders on {meta.get('days')} publication days.",
            f"- Awards read for outcomes: published up to {meta.get('awards_until')}.",
            f"- Suppliers found in the awards: {meta.get('suppliers')}. Eligible for the panel "
            f"(a company, at least {meta.get('min_past_wins')} wins before the cut, at least "
            f"{meta.get('min_test_wins')} wins of tenders in the test window): "
            f"{meta.get('eligible')}. On the panel: {meta.get('panel')}.",
            f"- Tenders the panel went on to win: {len(data['backtest_outcomes'])}.",
            f"- Embedding model: `{meta.get('embedder')}`.",
        ]
    else:
        lines.append("No backtest has been replayed yet.")
    lines += [
        "",
        "Every list holds at most the first "
        f"{settings.list_size} tenders. A tender counts as found if it is on the list the "
        "system wrote for that supplier on the day the tender was first published.",
        "",
        f"## Development months (before {data['held_out_start']})",
        "",
        "Ranker settings were chosen on these months.",
        "",
        table(data["development"]),
        "",
        _headline(data["development"]),
        "",
        f"## Held-out month (from {data['held_out_start']})",
        "",
        "Not looked at while settings were chosen. Scored once.",
        "",
        table(data["held_out"]) if any(s.outcomes for s in data["held_out"]) else "Not scored yet.",
        "",
        _headline(data["held_out"]),
        "",
        "## The model, on a sample",
        "",
    ]
    if model["assessments"]:
        decisions = model["decisions"]
        lines += [
            f"The model read the first {settings.assess_top} tenders of {model['lists']} lists: "
            f"{model['assessments']} assessments for US${model['cost_usd']:.2f} "
            f"(US${model['cost_usd'] / model['assessments'] * 1000:.2f} per 1,000). "
            "The other rankers are scored here on the same supplier-days. The sample was "
            "drawn separately in the development months and in the held-out month.",
            "",
            "Development months:",
            "",
            table(data["model_sample_development"]),
            "",
            "Held-out month:",
            "",
            table(data["model_sample_held_out"])
            if any(s.outcomes for s in data["model_sample_held_out"])
            else "Not scored yet.",
            "",
            f"- Decisions: {decisions.get('bid', 0)} bid, {decisions.get('ask_person', 0)} ask a "
            f"person, {decisions.get('skip', 0)} skip.",
            f'- Gate: {model["downgraded"]} decisions were changed to "ask a person" because '
            f"the quoted passage was not in the notice. {model['conditions_dropped']} stated "
            f"conditions were dropped for the same reason; {model['conditions_kept']} were kept.",
        ]
    else:
        lines.append("No model assessments are in the log.")
    lines += ["", "## The worst error", ""]
    removed = _rule_counts(data["backtest_outcomes"])
    lines += [
        "A tender the supplier went on to win that it was never shown.",
        "",
        f"- Removed by a hard rule before ranking: {sum(removed.values())} of "
        f"{len(data['backtest_outcomes'])} "
        + (
            f"({', '.join(f'{rule}: {n}' for rule, n in removed.most_common())})."
            if removed
            else "."
        ),
    ]
    for label, key in (
        ("development months", "model_sample_development"),
        ("held-out month", "model_sample_held_out"),
    ):
        by_model = _by_ranker(data[key]).get(MODEL_RANKER)
        if by_model and by_model.outcomes:
            lines.append(
                f'- Marked "skip" by the model, {label}: {by_model.skipped_by_model} of '
                f"{by_model.outcomes} won tenders in the sample."
            )
    lines += ["", "## Live", ""]
    live_lists = data["live_lists"]
    if live_lists:
        days = sorted({s.day for s in live_lists})
        since = min(s.written_at for s in live_lists).date()
        lines += [
            f"- Live since {since}, starting with the tenders published on {days[0]}; "
            f"{len(days)} publication days processed, {len(live_lists)} lists written.",
            f"- Outcomes that have arrived for tenders listed live: {len(data['live_outcomes'])}.",
        ]
        if any(s.outcomes for s in data["live"]):
            lines += ["", table(data["live"])]
        else:
            lines.append(
                "- None can be scored yet. In the backtest the median award came about four "
                "months after its tender, so the first live scores are expected about four "
                "months after the start."
            )
    else:
        lines.append("The live run has not started.")
    lines += [
        "",
        "## How far to trust this",
        "",
        "- Winners are the only outcome on record. A tender a supplier bid for and lost was "
        "relevant to it but counts here as noise in the list, so these figures understate how "
        "useful a list is and say nothing about precision.",
        "- A supplier is on the panel only if it won at least two tenders in the test window. "
        "The panel is therefore made of active suppliers, and takes them country by country, "
        "so small countries weigh more than their share of tenders.",
        "- Only tenders awarded by the day the awards were read can be scored. Procedures that "
        "take longer to award are missing.",
        "- Supplier identity is resolved from names and registration numbers typed by buyers. "
        "Some wins are attributed to the wrong supplier or to none.",
        "- The backtest replays history through the live pipeline in date order, with profiles "
        "frozen at the cut date. It is still a backtest: the live log is the measurement that "
        "cannot have been tuned.",
        "",
    ]
    return "\n".join(lines)


def write(settings: Settings, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(settings), encoding="utf-8")
    return out
