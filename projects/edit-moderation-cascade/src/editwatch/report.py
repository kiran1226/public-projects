"""The evaluation report, written from the committed logs.

Everything here reads revision ids, scores and outcomes. No page text is needed, so
the report can be rebuilt by anyone, without a key and without the wiki.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from .config import Settings
from .evals import SHARES, Score, arrival_order, at_share, percentile, score
from .log import PredictionLog
from .models import Prediction

CONFIG_LABEL = {
    "arrival": "Arrival order (what reviewers have today)",
    "wikimedia": "Wikimedia's revert-risk model",
    "tier1": "Tier 1 alone (event fields, no model)",
    "cascade": "Cascade (live)",
    "tier1-live": "Tier 1 alone (live, no model key)",
}
ORDER = ["arrival", "wikimedia", "tier1", "cascade-haiku", "cascade-sonnet", "cascade-opus",
         "every-haiku", "cascade", "tier1-live"]  # fmt: skip
SLICES = (0.05, 0.10, 0.20, 0.40)


def _read_json(path: Path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def load_outcomes(settings: Settings) -> dict[int, bool]:
    path = settings.log_dir / "outcomes-backtest.json.gz"
    if not path.exists():
        return {}
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    outcomes = dict.fromkeys(payload["kept"], False)
    outcomes.update(dict.fromkeys(payload["reverted"], True))
    return outcomes


def _ordered(scores: list[Score], meta: dict) -> list[Score]:
    runs = meta.get("versions", {})
    position = {name: i for i, name in enumerate(ORDER)}
    return sorted(
        scores,
        key=lambda s: (
            position.get(s.config, 99), runs.get(s.system_version, {}).get("run", 1),
            s.system_version,
        ),
    )  # fmt: skip


def name_of(s: Score, meta: dict) -> str:
    """A configuration in words. For a cascade: which model reads which share."""
    entry = meta.get("versions", {}).get(s.system_version, {})
    run = entry.get("run", 1)
    label = CONFIG_LABEL.get(s.config)
    if label is None:
        model = s.config.split("-", 1)[-1].capitalize()
        part = entry.get("share")
        if s.config.startswith("every-") or part == 1.0:
            label = f"{model} reads every edit"
        elif part is not None:
            label = f"Cascade, {model} reads the top {part:.0%}"
        else:
            label = f"Cascade, {model}"
    return f"{label}, run {run}" if run > 1 else label


def collect(settings: Settings) -> dict:
    """Every number the report and the scoreboard show."""
    from .live import live_paths, read_runs
    from .truth import OutcomeLog

    log_dir = settings.log_dir
    meta = _read_json(log_dir / "backtest-meta.json", {"versions": {}})
    tier1 = _read_json(settings.home / "data" / "tier1.json", {})
    samples = _read_json(settings.home / "data" / "samples.json", {})
    outcomes = load_outcomes(settings)
    predictions = PredictionLog(log_dir / "backtest.jsonl.gz").read()
    data: dict = {"settings": settings, "meta": meta, "tier1": tier1}
    data["loadtest"] = _read_json(settings.home / "data" / "loadtest.json", {})
    data["loadtest_every"] = _read_json(settings.home / "data" / "loadtest-every.json", {})
    by_config: dict[str, list[Prediction]] = {}
    for p in predictions:
        by_config.setdefault(p.config, []).append(p)
    data["by_config"] = by_config

    for part in ("held_out", "development"):
        ids = set(samples.get(part, []))
        rows = [p for p in predictions if p.rev_id in ids and p.config != "wikimedia"]
        scores = score(rows, outcomes)
        # Wikimedia's model could not score every revision; it is scored on those it did.
        scores += score([p for p in by_config.get("wikimedia", []) if p.rev_id in ids], outcomes)
        n = len([r for r in ids if r in outcomes])
        reverted = sum(outcomes[r] for r in ids if r in outcomes)
        if n:
            scores.append(arrival_order(n, reverted))
        data[part] = _ordered(scores, meta)
        data[f"{part}_n"] = n
        data[f"{part}_reverted"] = reverted
        # The same cascades with a smaller slice.
        quantiles = {float(k): v for k, v in tier1.get("quantiles", {}).items()}
        slices = []
        for config in ("every-haiku", "cascade-sonnet", "cascade-opus"):
            first = next((s for s in data[part] if s.config == config), None)
            if first is None:
                continue
            config_rows = [
                p for p in rows if p.config == config and p.system_version == first.system_version
            ]
            # A slice can only be made smaller than the one the model was run on.
            widest = meta.get("versions", {}).get(first.system_version, {}).get("share") or 1.0
            for share in SLICES:
                if share in quantiles and share <= widest:
                    smaller = at_share(config_rows, quantiles[share], outcomes, config)
                    slices.append((config, share, smaller))
        data[f"{part}_slices"] = slices

    held_start = settings.held_out_start
    for part, keep in (("held_out", lambda p: p.made_at >= held_start),
                       ("development", lambda p: p.made_at < held_start)):  # fmt: skip
        rows = [p for p in by_config.get("tier1", []) if keep(p)]
        data[f"{part}_all"] = score(rows, outcomes)
        n = len([p for p in rows if p.rev_id in outcomes])
        if n:
            data[f"{part}_all"].append(arrival_order(n, sum(outcomes[p.rev_id] for p in rows)))

    paths = live_paths(settings)
    live_predictions = PredictionLog(paths["log"]).read()
    live_outcomes = {o.rev_id: o for o in OutcomeLog(paths["outcomes"]).read().values()}
    data["live_predictions"] = live_predictions
    data["live_outcomes"] = live_outcomes
    data["live_runs"] = read_runs(settings)
    found = {r: o.reverted for r, o in live_outcomes.items() if o.found}
    data["live"] = score(live_predictions, found)
    if found:
        data["live"].append(arrival_order(len(found), sum(found.values())))
    return data


# -- rendering --------------------------------------------------------------------


def share(part: int, whole: int) -> str:
    return f"{part:,} of {whole:,} ({100 * part / whole:.0f}%)" if whole else "n/a"


def ranking_table(scores: list[Score], meta: dict) -> list[str]:
    head = "| Configuration | Edits | Reverted |"
    head += "".join(f" In the top {s:.0%} |" for s in SHARES)
    head += " **Reverted, in the bottom half** | Read by a model | Cost per 1,000 edits |"
    lines = [head, "|---|---:|---:|" + "---:|" * (len(SHARES) + 3)]
    for s in scores:
        cost = f"US${s.cost_per_1000:.2f}" if s.cost_usd else "none"
        cells = "".join(f" {share(s.hits[k], s.reverted)} |" for k in SHARES)
        lines.append(
            f"| {name_of(s, meta)} | {s.edits:,} | {s.reverted:,} |{cells} "
            f"**{s.bottom_half}** | {s.model_read:,} | {cost} |"
        )
    return lines


def slice_table(slices: list[tuple[str, float, Score]]) -> list[str]:
    lines = [
        "| Model | Share of edits it reads | Edits read | In the top 2% | In the top 5% | "
        "In the top 10% | Cost per 1,000 edits |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for config, part, s in slices:
        cost = f"US${s.cost_per_1000:.2f}" if s.cost_usd else "none"
        lines.append(
            f"| {config.split('-', 1)[-1].capitalize()} | {part:.0%} | {s.model_read:,} | "
            f"{share(s.hits[0.02], s.reverted)} | {share(s.hits[0.05], s.reverted)} | "
            f"{share(s.hits[0.10], s.reverted)} | {cost} |"
        )
    return lines


def details(scores: list[Score], meta: dict) -> list[str]:
    lines = []
    for s in scores:
        parts = []
        if s.gate:
            parts.append("gate: " + ", ".join(f"{k} {v}" for k, v in sorted(s.gate.items())))
        if s.skipped:
            parts.append("not decided by the model: " + ", ".join(
                f"{k} {v}" for k, v in sorted(s.skipped.items())))  # fmt: skip
        if s.categories:
            parts.append("categories: " + ", ".join(
                f"{k} {v}" for k, v in s.categories.most_common()))  # fmt: skip
        if parts:
            lines.append(f"- {name_of(s, meta)}: " + "; ".join(parts) + ".")
    return lines or ["- Nothing to report."]


def loadtest_table(load: dict) -> list[str]:
    lines = [
        "| Replay speed | Edits a second | Read by the model | Skipped: queue full | "
        "Seconds to the queue, model tier, median | ...95th percentile | Longest queue |",
        "|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in load.get("results", []):
        lines.append(
            f"| {r['speed']:g}x | {r['edits_per_second']} | {r['read_by_model']} | "
            f"{r['skipped_queue_full']} | {r['model_p50_s']} | {r['model_p95_s']} | "
            f"{r['longest_wait']} |"
        )
    return lines


def section(title: str, data: dict, part: str) -> list[str]:
    meta = data["meta"]
    scores = data.get(part, [])
    if not scores:
        return [f"## {title}", "", "Not scored yet.", ""]
    lines = [
        f"## {title}",
        "",
        f"{data[f'{part}_n']:,} edits drawn at random, {data[f'{part}_reverted']} of them "
        "reverted within 48 hours. Every configuration orders the same edits.",
        "",
        *ranking_table(scores, meta),
        "",
    ]
    if data.get(f"{part}_slices"):
        lines += [
            "The same cascades with a smaller slice:",
            "",
            *slice_table(data[f"{part}_slices"]),
            "",
        ]
    lines += ["Gate, skips and categories:", "", *details(scores, meta), ""]
    return lines


def render(data: dict) -> str:
    settings: Settings = data["settings"]
    meta = data["meta"]
    windows = data["tier1"].get("windows", {})

    def window(name: str) -> str:
        w = windows.get(name, {})
        return f"{w.get('edits', 0):,} edits by people, {w.get('reverted', 0):,} reverted"

    lines = [
        "# Evaluation report",
        "",
        "Written by `editwatch eval` from the committed logs. Do not edit by hand.",
        "",
        "## What was scored",
        "",
        f"- Wiki: `{settings.wiki_host}`, article edits by people. History is sampled: "
        f"{settings.slice_edits} edits every {settings.slice_hours} hours.",
        f"- Training, {settings.history_start:%d %B} to {settings.cut:%d %B %Y}: "
        f"{window('training')}.",
        f"- Development days, to {settings.held_out_start:%d %B}: {window('development')}.",
        f"- Held-out days, to {settings.test_end:%d %B}: {window('held_out')}. "
        "Scored once, after every setting was fixed.",
        f"- An edit counts as bad if it carried the tag `mw-reverted` at least "
        f"{settings.truth_after_hours} hours after it was made.",
        "",
    ]
    lines += section("Held-out days, the model sample", data, "held_out")
    lines += section("Development days, the model sample", data, "development")
    for title, part in (("Held-out days, every edit", "held_out_all"),
                        ("Development days, every edit", "development_all")):  # fmt: skip
        if data.get(part):
            lines += [f"## {title}", "", *ranking_table(_ordered(data[part], meta), meta), ""]
    load = data["loadtest"]
    if load:
        lines += [
            "## Does it keep up?",
            "",
            f"A recording of {load['edits']} edits over {load['minutes']} minutes, replayed on a "
            f"virtual clock with measured request and model times, "
            f"{load.get('replays', 1)} times at each speed with a new draw of those times. "
            f"Counts are per replay. Latency target: "
            f"{load['target_s']:g} seconds at the 95th percentile.",
            "",
            *loadtest_table(load),
            "",
            f"Fastest replay inside the target with nothing skipped: {load['sustained']}.",
            "",
        ]
    every = data.get("loadtest_every")
    if every:
        lines += [
            "The same recording with the model reading every edit:",
            "",
            *loadtest_table(every),
            "",
            f"Fastest replay inside the target with nothing skipped: {every['sustained']}.",
            "",
        ]
    lines += ["## Live", ""]
    live: list[Prediction] = data["live_predictions"]
    if not live:
        lines += ["The live run has not started.", ""]
    else:
        runs = data["live_runs"]
        read = sum(1 for p in live if p.verdict.source == "model")
        model_lat = [p.latency_s for p in live if p.verdict.source == "model" and p.latency_s]
        lines += [
            f"- Live since {min(p.written_at for p in live):%Y-%m-%d}: {len(live):,} edits "
            f"in {len(runs)} runs, {read:,} read by the model.",
            f"- Seconds from event to queue for edits the model read: median "
            f"{percentile(model_lat, 0.5) or 0:.1f}, 95th percentile "
            f"{percentile(model_lat, 0.95) or 0:.1f}.",
            f"- Outcomes read so far: {len(data['live_outcomes']):,}. An outcome is read "
            f"{settings.truth_after_hours} hours after the edit.",
            "",
        ]
        if data["live"]:
            lines += ranking_table(_ordered(data["live"], meta), meta) + [""]
    lines += [
        "## Reading these numbers",
        "",
        '- "Reverted" is not "vandalism". Good-faith mistakes are reverted, some reverts are '
        "disputes between editors, and damage nobody noticed counts as fine.",
        "- Arrival order is an expectation, not a measurement: a queue that ignores risk "
        "holds k% of the reverted edits in its first k%.",
        "- Wikimedia's model was built for every language and for patrolling tools. The "
        "comparison shows where this system stands, not that one is better.",
        "- The model sample is small. A difference of a few edits between two "
        "configurations is inside what a repeat run of one configuration gives.",
        "",
    ]
    return "\n".join(lines)
