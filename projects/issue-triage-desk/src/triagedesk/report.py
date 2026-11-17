"""The evaluation report, written from the committed logs.

Everything here reads issue numbers, labels and the desk's answers. No issue text is
needed, so the report can be rebuilt by anyone, without a key and without the tracker.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from .config import Settings, day_start
from .evals import HIT_DEPTHS, Score, families_from, score, wilson_interval
from .log import PredictionLog
from .models import Outcome, Prediction, Taxonomy
from .pipeline import CONFIG_LABEL, CONFIGS
from .taxonomy import load as load_taxonomy
from .truth import NOT_FETCHED, OUTSIDE, RESOLVED, UNRESOLVED, OutcomeLog


def _read_json(path: Path, default):
    return json.loads(path.read_text("utf-8")) if path.exists() else default


def _order(scores: list[Score], meta: dict) -> list[Score]:
    position = {name: i for i, name in enumerate(CONFIGS)}
    runs = meta.get("versions", {})
    return sorted(
        scores,
        key=lambda s: (
            position.get(s.config, 99),
            runs.get(s.system_version, {}).get("run", 1),
            s.system_version,
        ),
    )


def truth_summary(outcomes: list[Outcome], primary_prefix_labels: set[str]) -> dict:
    duplicates = [o for o in outcomes if o.duplicate]
    return {
        "issues": len(outcomes),
        "with_primary_label": sum(1 for o in outcomes if set(o.labels) & primary_prefix_labels),
        "with_several_primary": sum(
            1 for o in outcomes if len(set(o.labels) & primary_prefix_labels) > 1
        ),
        "duplicates": len(duplicates),
        "resolved": sum(1 for o in duplicates if o.original_status == RESOLVED),
        "outside": sum(1 for o in duplicates if o.original_status == OUTSIDE),
        "unresolved": sum(1 for o in duplicates if o.original_status == UNRESOLVED),
        "not_fetched": sum(1 for o in duplicates if o.original_status == NOT_FETCHED),
    }


def collect(settings: Settings) -> dict:
    """Every number the report and the scoreboard show."""
    from .live import live_paths, read_runs

    log_dir = settings.log_dir
    meta = _read_json(log_dir / "backtest-meta.json", {})
    data: dict = {"meta": meta, "settings": settings}
    primary = settings.groups[0].name

    taxonomy_path = settings.home / "data" / "taxonomy.json"
    taxonomy = load_taxonomy(taxonomy_path) if taxonomy_path.exists() else Taxonomy(repo="")
    data["taxonomy"] = taxonomy
    primary_names = set(taxonomy.names(primary))

    predictions = PredictionLog(log_dir / "backtest.jsonl.gz").read()
    outcomes = OutcomeLog(log_dir / "outcomes-backtest.jsonl").read()
    families = families_from(outcomes)
    held_out_from = day_start(settings.held_out_start)

    def part(held_out: bool) -> tuple[list[Prediction], dict[int, Outcome]]:
        chosen = {n: o for n, o in outcomes.items() if (o.created_at >= held_out_from) == held_out}
        return [p for p in predictions if p.number in chosen], chosen

    for name, held_out in (("development", False), ("held_out", True)):
        chosen_predictions, chosen_outcomes = part(held_out)
        data[name] = _order(
            score(chosen_predictions, chosen_outcomes, taxonomy, primary, families), meta
        )
        data[f"{name}_truth"] = truth_summary(list(chosen_outcomes.values()), primary_names)
    data["backtest_predictions"] = len(predictions)

    paths = live_paths(settings)
    live_taxonomy_path = settings.home / "data" / "taxonomy-live.json"
    live_taxonomy = load_taxonomy(live_taxonomy_path) if live_taxonomy_path.exists() else taxonomy
    live_predictions = PredictionLog(paths["log"]).read()
    live_outcomes = OutcomeLog(paths["outcomes"]).read()
    data["live_predictions"] = live_predictions
    data["live_outcomes"] = live_outcomes
    data["live_runs"] = read_runs(settings)
    data["live"] = _order(
        score(
            live_predictions, live_outcomes, live_taxonomy, primary,
            families_from(live_outcomes), live=True,
        ),
        meta,
    )  # fmt: skip
    return data


# -- rendering --------------------------------------------------------------------


def share(part: int, whole: int) -> str:
    return f"{part:,} of {whole:,} ({100 * part / whole:.0f}%)" if whole else "n/a"


def percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def config_name(s: Score, meta: dict) -> str:
    entry = meta.get("versions", {}).get(s.system_version, {})
    run = entry.get("run", 1)
    name = CONFIG_LABEL.get(s.config, s.config)
    if entry.get("note"):
        # An earlier system version kept in the log, for example a first prompt.
        return f"{name} ({entry['note']})"
    return f"{name}, run {run}" if run > 1 else name


def label_table(scores: list[Score], meta: dict, primary: str) -> list[str]:
    lines = [
        f"| Configuration | Labels proposed | Right | Of the maintainers' labels found | "
        f"{primary.capitalize()} right | Applied alone | Wrong among those | "
        f"...on issues left unlabelled |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in scores:
        every, sure = s.all_labels(), s.all_labels(sure=True)
        lines.append(
            f"| {config_name(s, meta)} | {every.proposed:,} | "
            f"{share(every.right, every.proposed)} | "
            f"{share(every.right, every.present)} | {share(s.primary_right, s.labelled)} | "
            f"{sure.proposed} | {share(sure.wrong, sure.proposed)} | {s.sure_on_unlabelled} |"
        )
    return lines


def group_table(scores: list[Score], meta: dict) -> list[str]:
    groups = sorted({g for s in scores for g in s.labels})
    header = "| Configuration |" + "".join(
        f" {g}: right of proposed | {g}: found of present |" for g in groups
    )
    lines = [header, "|---|" + "---:|---:|" * len(groups)]
    for s in scores:
        row = f"| {config_name(s, meta)} |"
        for g in groups:
            c = s.labels[g]
            row += f" {share(c.right, c.proposed)} | {share(c.right, c.present)} |"
        lines.append(row)
    return lines


def duplicate_table(scores: list[Score], meta: dict) -> list[str]:
    lines = [
        "| Configuration | Called a duplicate | Right | Real duplicate, other issue named | "
        "**New issue called a duplicate** | ...of them at high confidence | "
        "Duplicates found |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for s in scores:
        lines.append(
            f"| {config_name(s, meta)} | {s.flagged} | {s.flagged_right} | "
            f"{s.flagged_other_issue + s.flagged_unresolved} | **{s.false_duplicates}** | "
            f"{s.false_duplicates_sure} | {share(s.flagged_right, s.duplicates)} |"
        )
    return lines


def retrieval_line(scores: list[Score]) -> str | None:
    """Where the original stands among the retrieved issues. The same for every configuration."""
    if not scores or not scores[0].duplicates:
        return None
    s = scores[0]
    parts = ", ".join(
        f"first {k}: {share(s.hits[k], s.duplicates)}" if k > 1 else
        f"first place: {share(s.hits[k], s.duplicates)}"
        for k in HIT_DEPTHS
    )  # fmt: skip
    return f"The original is among the earlier issues the search returns: {parts}."


def routing_table(scores: list[Score], meta: dict) -> list[str]:
    lines = [
        "| Configuration | Issues | Handled alone | ...with nothing wrong | To a person | "
        "Answered by the model | Cost per 1,000 issues | Seconds per issue |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in scores:
        cost = f"US${1000 * s.cost_usd / s.issues:.2f}" if s.cost_usd else "none"
        seconds = s.latency_ms / s.issues / 1000 if s.issues else 0.0
        lines.append(
            f"| {config_name(s, meta)} | {s.issues:,} | {share(s.alone, s.issues)} | "
            f"{share(s.alone_clean, s.alone)} | {share(s.person, s.issues)} | "
            f"{s.model_answers} | {cost} | {seconds:.2f} |"
        )
    return lines


def gate_lines(scores: list[Score], meta: dict) -> list[str]:
    lines = []
    for s in scores:
        if s.gate:
            fired = ", ".join(f"{rule} {count}" for rule, count in sorted(s.gate.items()))
            lines.append(f"- {config_name(s, meta)}: {fired}.")
    return lines or ["- No gate rule fired."]


def truth_lines(t: dict, primary: str) -> list[str]:
    return [
        f"- {t['issues']:,} issues. {t['with_primary_label']:,} carry at least one {primary} label "
        f"of the taxonomy, {t['with_several_primary']} carry several, "
        f"{t['issues'] - t['with_primary_label']} carry none.",
        f"- {t['duplicates']} were closed as duplicates. The original was resolved for "
        f"{t['resolved']}; {t['outside']} point to another repository, {t['unresolved']} name "
        f"no single earlier issue"
        + (f", {t['not_fetched']} timelines were not fetched" if t["not_fetched"] else "")
        + '. Only resolved duplicates count towards "duplicates found".',
    ]


def spread_lines(scores: list[Score], meta: dict) -> list[str]:
    """Repeat runs of one configuration, side by side.

    Only runs that answered the same number of issues are compared: a run over one
    month and a run over the whole period are not repeats of each other.
    """
    lines = []
    for config in CONFIGS:
        by_size: dict[int, list[Score]] = {}
        for s in scores:
            if s.config == config:
                by_size.setdefault(s.issues, []).append(s)
        for issues, runs in sorted(by_size.items()):
            if len(runs) < 2:
                continue
            right = [s.all_labels().right for s in runs]
            proposed = [s.all_labels().proposed for s in runs]
            primary = [s.primary_right for s in runs]
            false = [s.false_duplicates for s in runs]
            found = [s.flagged_right for s in runs]
            alone = [s.alone for s in runs]
            lines.append(
                f"- {CONFIG_LABEL[config]}, {len(runs)} runs on the same {issues:,} issues: "
                f"labels right {min(right)} to {max(right)} (of {min(proposed)} to "
                f"{max(proposed)} proposed), area right {min(primary)} to {max(primary)}, "
                f"new issues called duplicates {min(false)} to {max(false)}, duplicates "
                f"found {min(found)} to {max(found)}, handled alone {min(alone)} to "
                f"{max(alone)}."
            )
    return lines


def section(title: str, scores: list[Score], truth: dict, meta: dict, primary: str) -> list[str]:
    if not scores:
        return [f"## {title}", "", "Not scored yet.", ""]
    lines = [f"## {title}", "", *truth_lines(truth, primary), "", "### Labels", ""]
    lines += label_table(scores, meta, primary)
    lines += [
        "",
        f'"{primary.capitalize()} right" counts issues where every {primary} label the desk '
        f"proposed is on the issue and it proposed at least one, out of the issues the "
        f'maintainers gave one. "Applied alone" are labels at high confidence. A label on '
        "an issue the maintainers left without any label of the taxonomy counts as wrong; "
        "the last column says how many of the wrong ones are of that kind.",
        "",
        "Per kind of label:",
        "",
        *group_table(scores, meta),
        "",
        "### Duplicates",
        "",
        *duplicate_table(scores, meta),
        "",
    ]
    line = retrieval_line(scores)
    if line:
        lines += [line, ""]
    lines += ["### Routing and cost", "", *routing_table(scores, meta), ""]
    spread = spread_lines(scores, meta)
    if spread:
        lines += ["### Repeat runs", "", *spread, ""]
    lines += ["### Gate rules that fired", "", *gate_lines(scores, meta), ""]
    return lines


def headline(scores: list[Score], config: str) -> Score | None:
    """The first run of a configuration."""
    return next((s for s in scores if s.config == config), None)


def render(data: dict) -> str:
    settings: Settings = data["settings"]
    meta = data["meta"]
    primary = settings.groups[0].name
    taxonomy: Taxonomy = data["taxonomy"]
    lines = [
        "# Evaluation report",
        "",
        "Written by `triagedesk eval` from the committed logs. Do not edit by hand.",
        "",
        "## What was scored",
        "",
        f"- Tracker: `{settings.repo}`. History from {settings.history_start}: "
        f"{meta.get('stored_issues', 0):,} issues stored, "
        f"{meta.get('training_issues', 0):,} of them opened before the cut "
        f"({settings.cut}) and used for training.",
        f"- Taxonomy: {len(taxonomy.labels)} labels "
        f"({', '.join(f'{len(taxonomy.names(g.name))} {g.name}' for g in settings.groups)}). "
        f"{len(taxonomy.dropped)} labels were too rare to keep.",
        f"- Development months: issues opened {settings.cut} to the day before "
        f"{settings.held_out_start}. Settings were chosen here.",
        f"- Held-out months: issues opened {settings.held_out_start} to {settings.test_end}. "
        "Not looked at while settings were chosen.",
        "- Truth: the labels on each issue and its close reason, read on "
        f"{meta.get('truth_read', 'the day of the backfill')}, at least "
        f"{settings.truth_after_days} days after it was opened.",
        "",
    ]
    lines += section("Held-out months", data["held_out"], data["held_out_truth"], meta, primary)
    lines += section(
        "Development months", data["development"], data["development_truth"], meta, primary
    )

    lines += ["## Live", ""]
    live_predictions: list[Prediction] = data["live_predictions"]
    if not live_predictions:
        lines += ["The live run has not started.", ""]
    else:
        first = min(p.written_at for p in live_predictions)
        issues = {p.number for p in live_predictions}
        early = {p.number for p in live_predictions if not (p.seen_labels or p.seen_closed)}
        lines += [
            f"- Live since {first:%Y-%m-%d}. {len(issues)} issues answered in "
            f"{len(data['live_runs'])} runs; {len(early)} of them before the tracker showed a "
            "label of the taxonomy or a close.",
            f"- Outcomes read so far: {len(data['live_outcomes'])}. An outcome is read "
            f"{settings.truth_after_days} days after an issue opens.",
            "",
        ]
        if data["live"] and any(s.issues for s in data["live"]):
            lines += label_table(data["live"], meta, primary) + [""]
            lines += duplicate_table(data["live"], meta) + [""]
        else:
            lines += ["No live prediction has an outcome yet.", ""]
    lines += [
        "## Reading these numbers",
        "",
        "- The maintainers' labels are the reference, not the truth. They leave some issues "
        "unlabelled and miss some duplicates, and both count against the desk.",
        '- A "new issue called a duplicate" may be a real duplicate nobody closed as one. '
        "The count is an upper bound on the worst error.",
        "- Backtest titles and bodies are as the tracker shows them today. A few were edited "
        "after they were submitted.",
        "- Backtest and live numbers are never added together.",
        "",
    ]
    return "\n".join(lines)


def write(settings: Settings, path: Path | None = None) -> Path:
    path = path or settings.home / "docs" / "eval-report.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(collect(settings)), encoding="utf-8")
    return path


def interval_text(successes: int, total: int) -> str:
    low, high = wilson_interval(successes, total)
    return f"{100 * low:.0f}% to {100 * high:.0f}%"


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")
