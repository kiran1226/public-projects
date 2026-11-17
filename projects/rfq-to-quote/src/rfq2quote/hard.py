"""The harder set: every recorded run on it, replayed through today's rules.

The 40 original requests are at the ceiling for every model configuration, so they
cannot separate one from another. The harder set is a third evaluation set (split
`hard`) built to try: its labels were chosen in code, and a model wrote each request
around the product and quantity wordings the code fixed.

Recorded runs on it are kept apart from the published recording of the original
requests: one run per configuration in `data/cassettes/hard/<model>.json`, and five
further runs of the default configuration in `data/repeat-runs/hard/`.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .evals import load_gold, score_case
from .llm import DEFAULT_MODEL
from .pipeline import DATA_DIR, build_pipeline
from .repeats import RepeatRun, replay_pipeline, score_all, summarise_run

SPLIT = ("hard",)
# (configuration, model) of the runs that were recorded once each.
SINGLE_RUNS = [
    ("llm-v1", DEFAULT_MODEL),
    ("llm-v2", DEFAULT_MODEL),
    ("llm-v2", "claude-sonnet-5-5"),
    ("llm-v2", "claude-opus-5-5"),
]


def score_hard(data_dir: Path = DATA_DIR) -> list[RepeatRun]:
    """One row for the rules alone, one per recorded configuration, one per repeat run."""
    gold = load_gold(data_dir, SPLIT)

    def score(pipeline):
        return [score_case(pipeline, g, data_dir) for g in gold]

    rows = [summarise_run("baseline", "", 0, score(build_pipeline("baseline", data_dir=data_dir)))]
    for config, model in SINGLE_RUNS:
        path = data_dir / "cassettes" / "hard" / f"{model}.json"
        pipeline = replay_pipeline(path, model, data_dir, config.removeprefix("llm-"))
        rows.append(summarise_run(f"{config}, {model}", model, 0, score(pipeline)))
    for run in score_all(data_dir / "repeat-runs" / "hard", data_dir, SPLIT):
        rows.append(replace(run, label=f"llm-v2, {run.model}, repeat {run.number}"))
    return rows


def table(rows: list[RepeatRun]) -> str:
    """The runs as a Markdown table of counts."""
    lines = [
        "| Run | Requests with every line right | Lines right | Must-review quotes held | "
        "Unsafe auto-approvals | Clean quotes held | Cost per request |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in rows:
        cost = f"${run.cost_per_request:.4f}" if run.cost_per_request else "none"
        lines.append(
            f"| {run.label} | {run.lines_exact} of {run.requests} | "
            f"{run.lines_right} of {run.lines} | {run.must_review_held} of {run.must_review} | "
            f"{run.unsafe} | {run.clean_held} of {run.clean} | {cost} |"
        )
    return "\n".join(lines)
