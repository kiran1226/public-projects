"""Repeat runs: the same evaluation recorded several times, replayed through today's rules.

One recorded run cannot show how much a model's answers move from run to run.
`data/repeat-runs/` holds further live runs on the 40 original requests, one file
per run, named `<model>-run<n>.json`. Replaying them costs nothing and applies the
current guardrails, so a rule added after a run was recorded is tested against what
the model answered then. (`hard.py` does the same for the harder set.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .evals import ORIGINAL_SPLITS, CaseScore, load_gold, score_case
from .extract import LLMExtractor
from .guardrails import Policy
from .llm import AnthropicProvider, CassetteProvider
from .match import LLMMatcher
from .pipeline import DATA_DIR, Pipeline, load_catalog

REPEATS_DIR = DATA_DIR / "repeat-runs"
_NAME = re.compile(r"(?P<model>.+)-run(?P<n>\d+)")


@dataclass(frozen=True)
class RepeatRun:
    label: str
    model: str
    number: int
    requests: int
    lines_exact: int  # requests with every line right
    lines: int  # labelled lines
    lines_right: int
    must_review: int
    must_review_held: int
    clean: int
    clean_held: int
    unsafe: int
    size_mismatch: list[str]  # requests the size rule held
    cost_per_request: float


def replay_pipeline(
    path: Path, model: str, data_dir: Path = DATA_DIR, prompt_version: str = "v2"
) -> Pipeline:
    """A pipeline that answers from one recording only. A missing response is an error."""
    defaults = AnthropicProvider(model)  # for the request parameters only; never called
    provider = CassetteProvider(
        path,
        model,
        inner=None,
        params={"effort": defaults.effort, "max_tokens": defaults.max_tokens},
    )
    catalog = load_catalog(data_dir)
    return Pipeline(
        LLMExtractor(provider, prompt_version),
        LLMMatcher(catalog, provider),
        catalog,
        Policy(),
        max_workers=1,
    )


def summarise_run(label: str, model: str, number: int, cases: list[CaseScore]) -> RepeatRun:
    must = [c for c in cases if c.expected_route == "human_review"]
    clean = [c for c in cases if c.expected_route == "auto_approve"]
    return RepeatRun(
        label=label,
        model=model,
        number=number,
        requests=len(cases),
        lines_exact=sum(c.lines_exact for c in cases),
        lines=sum(c.n_gold for c in cases),
        lines_right=sum(c.line_tp for c in cases),
        must_review=len(must),
        must_review_held=sum(c.route == "human_review" for c in must),
        clean=len(clean),
        clean_held=sum(c.route == "human_review" for c in clean),
        unsafe=sum(c.unsafe_auto_approval for c in cases),
        size_mismatch=sorted(c.id for c in cases if "SIZE_MISMATCH" in c.flags),
        cost_per_request=sum(c.usage.cost_usd for c in cases) / len(cases),
    )


def score_recording(
    path: Path, data_dir: Path = DATA_DIR, splits: tuple[str, ...] = ORIGINAL_SPLITS
) -> RepeatRun:
    """Replay one recorded run on the requests of the given sets."""
    name = _NAME.fullmatch(path.stem)
    if name is None:
        raise ValueError(f"{path.name}: expected <model>-run<n>.json")
    pipeline = replay_pipeline(path, name["model"], data_dir)
    cases = [score_case(pipeline, gold, data_dir) for gold in load_gold(data_dir, splits)]
    return summarise_run(path.stem, name["model"], int(name["n"]), cases)


def score_all(
    directory: Path = REPEATS_DIR,
    data_dir: Path = DATA_DIR,
    splits: tuple[str, ...] = ORIGINAL_SPLITS,
) -> list[RepeatRun]:
    paths = sorted(directory.glob("*-run*.json"))
    runs = [score_recording(path, data_dir, splits) for path in paths]
    return sorted(runs, key=lambda run: (run.model, run.number))


def table(runs: list[RepeatRun]) -> str:
    """The runs as a Markdown table."""
    rows = [
        "| Run | Requests with every line right | Must-review quotes held | "
        "Clean quotes held | Unsafe auto-approvals | Held by the size rule | Cost per request |",
        "|---|---|---|---|---|---|---|",
    ]
    for run in runs:
        rows.append(
            f"| {run.label} | {run.lines_exact} of {run.requests} | "
            f"{run.must_review_held} of {run.must_review} | {run.clean_held} of {run.clean} | "
            f"{run.unsafe} | {', '.join(run.size_mismatch) or 'none'} | "
            f"${run.cost_per_request:.4f} |"
        )
    return "\n".join(rows)
