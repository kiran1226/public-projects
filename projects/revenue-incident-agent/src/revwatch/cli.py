"""Command line: build the worlds, replay one into the review queue, score everything."""

from __future__ import annotations

import json
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Annotated, NoReturn

import typer
import uvicorn

from mock_admin.app import create_app
from mock_admin.seed import entities_from_world
from revwatch.detectors.base import Detector
from revwatch.detectors.gbm import GbmDetector
from revwatch.detectors.seasonal import SeasonalDetector
from revwatch.detectors.static import StaticDetector
from revwatch.evals import DetectionReport, confirm_from_labels, render_report, score_detection
from revwatch.investigate.agent import AgentInvestigator
from revwatch.investigate.base import Case, Investigator, StopRun
from revwatch.investigate.script import ScriptInvestigator
from revwatch.investigation_evals import (
    InvestigationReport,
    cases_from_labels,
    labels_for,
    ongoing_at,
    render_investigation,
    score_investigation,
)
from revwatch.llm import (
    DEFAULT_EFFORT,
    DEFAULT_MODEL,
    AnthropicProvider,
    CappedProvider,
    CassetteProvider,
)
from revwatch.pipeline import DetectionRun, run_detection
from revwatch.policy import DetectionPolicy
from revwatch.repeats import Replayed, recordings, replay_each, summarise, table
from revwatch.replay import handle, queue
from revwatch.scenarios import Label, load_labels, load_scenarios
from revwatch.store import Store, StoreError
from revwatch.warehouse import Warehouse
from revwatch.world import WorldConfig, generate_world

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Revenue incident agent.")

PROJECT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT = PROJECT / "docs" / "eval-report.md"
DEFAULT_STORE = PROJECT / "var" / "revwatch.sqlite"
ADMIN_STATE = PROJECT / "var" / "mock_admin.sqlite"
SEEDS = {"dev": 20261115, "heldout": 20261117}
EVERY = "all"
DETECTORS: dict[str, Callable[[], Detector]] = {
    "static": StaticDetector,
    "seasonal": SeasonalDetector,
    "gbm": GbmDetector,
}


INVESTIGATORS = ("script", "agent")
CASSETTES = PROJECT / "data" / "cassettes"
REPEATS = PROJECT / "data" / "repeat-runs"
# Both worlds recorded from nothing cost US$0.12 on the default model and US$1.01 on
# `claude-sonnet-5-5`. A run that would cost more than this has to be asked for.
DEFAULT_MAX_USD = 2.0
MAX_USD_HELP = (
    "With --live: stop sending once live calls have cost this many US dollars. Calls "
    "already in flight finish, so a run can pass it by one call per concurrent investigation."
)


@dataclass(frozen=True)
class ModelOptions:
    """How the agent reaches its model. By default it only replays what was recorded."""

    live: bool = False
    record: bool = False
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    concurrency: int = 8
    max_usd: float = DEFAULT_MAX_USD

    @cached_property
    def provider(self) -> CappedProvider | None:
        """The live provider. One per command, so the spend limit covers every world."""
        if not self.live:
            return None
        return CappedProvider(AnthropicProvider(concurrency=self.concurrency), self.max_usd)

    def spent(self) -> str | None:
        """What the live calls of this command cost, as a line to print."""
        if self.provider is None:
            return None
        return (
            f"live calls cost US${self.provider.spent_usd:.4f} "
            f"(limit US${self.max_usd:.2f}, --max-usd)"
        )


def _agent(world_name: str, options: ModelOptions) -> AgentInvestigator | None:
    """The agent for one world, or None when it has no recording and live calls are off."""
    path = CASSETTES / f"{world_name}.json"
    if not options.live and not path.exists():
        return None
    provider = CassetteProvider(path, options.provider, record=options.record)
    return AgentInvestigator(provider, options.model, options.effort)


def _fail(message: str) -> NoReturn:
    typer.echo(f"error: {message}", err=True)
    raise typer.Exit(2)


def _world_path(world: str) -> Path:
    return PROJECT / "data" / "worlds" / f"{world}.duckdb"


def _scenario_path(world: str, scenarios: Path | None) -> Path:
    path = scenarios or PROJECT / "data" / "scenarios" / f"{world}.yaml"
    if not path.exists():
        _fail(f"scenario file not found: {path}")
    return path


@app.command()
def generate(
    world: Annotated[str, typer.Option(help="World name (dev, heldout), or all for both.")] = EVERY,
    scenarios: Annotated[Path | None, typer.Option(help="Scenario file to use instead.")] = None,
    out: Annotated[Path | None, typer.Option(help="Where to write the world.")] = None,
    days: Annotated[int, typer.Option(help="Length of the world in days.")] = 140,
    seed: Annotated[
        int | None, typer.Option(help="Seed to use instead of the world's own.")
    ] = None,
) -> None:
    """Build the synthetic worlds from their seeds and scenario files."""
    custom = scenarios is not None or out is not None
    if world == EVERY and not custom:
        names = list(SEEDS)
    else:
        names = ["dev" if world == EVERY else world]
    for name in names:
        scenario_path = _scenario_path(name, scenarios)
        if seed is None and name not in SEEDS:
            _fail(f"no seed is defined for world {name!r}; pass --seed")
        target = out or _world_path(name)
        try:
            loaded = load_scenarios(scenario_path)
            config = WorldConfig(seed=SEEDS[name] if seed is None else seed, days=days)
            labels = generate_world(config, loaded, target)
        except ValueError as exc:  # includes pydantic's ValidationError
            _fail(str(exc))
        labelled = sum(label.labelled for label in labels)
        typer.echo(f"wrote {target} ({days} days, {labelled} labelled scenarios)")


def _score(
    world_path: Path, labels: Sequence[Label], names: Sequence[str], prefix: str = ""
) -> tuple[list[DetectionReport], dict[str, DetectionRun], str, int]:
    """Run the named detectors over one world: (reports, runs, world hash, hours in the world)."""
    reports: list[DetectionReport] = []
    runs: dict[str, DetectionRun] = {}
    with Warehouse(world_path) as warehouse:
        confirm = confirm_from_labels(labels, warehouse.start)
        for name in names:
            run = run_detection(
                warehouse,
                DETECTORS[name](),
                confirm=confirm,
                progress=lambda message: typer.echo(prefix + message),
            )
            report = score_detection(run, labels, warehouse.start)
            reports.append(report)
            runs[name] = run
            typer.echo(
                f"{prefix}{name}: detected {report.detected} of {report.n_incidents}, "
                f"{report.false_incidents} false incidents, {report.duplicates} duplicates"
            )
        return reports, runs, warehouse.world_hash, warehouse.n_hours


View = tuple[str, list[Case], Mapping[str, Label | None]]


def _views(
    world_path: Path, labels: Sequence[Label], run: DetectionRun
) -> tuple[list[View], dict[str, list[Label]]]:
    """What investigation is scored on: (view, cases, what each case is an alert on) twice.

    End to end, the alerts of `run`; in isolation, one case per labelled incident.
    Also, per alert, the incidents that were going on when it was raised.
    """
    with Warehouse(world_path) as warehouse:
        start = warehouse.start
    alerts = sorted(
        (i for i in run.incidents if i.opened_tick >= run.scored_from),
        key=lambda i: (i.opened_tick, i.incident_id),
    )
    isolated = cases_from_labels(labels, start)
    by_case = {
        case.incident_id: next(lb for lb in labels if lb.scenario_id == scenario_id)
        for scenario_id, case in isolated.items()
    }
    views: list[View] = [
        ("end to end", [Case.of(i) for i in alerts], labels_for(alerts, labels, start)),
        ("in isolation", list(isolated.values()), by_case),
    ]
    return views, ongoing_at(alerts, labels, start)


def _investigate(
    world_path: Path,
    labels: Sequence[Label],
    run: DetectionRun,
    agent: AgentInvestigator | None,
    concurrency: int,
) -> list[InvestigationReport]:
    """Score every investigator on the alerts of `run`, and on one case per labelled incident."""
    investigators: list[tuple[Investigator, int]] = [(ScriptInvestigator(), 1)]
    if agent is not None:
        investigators.append((agent, concurrency))
    weeks = (run.n_hours - run.scored_from) / 168
    views, ongoing = _views(world_path, labels, run)
    reports: list[InvestigationReport] = []
    for investigator, at_a_time in investigators:
        for view, cases, truth in views:
            handled = handle(
                world_path, cases, investigator, concurrency=at_a_time, progress=typer.echo
            )
            report = score_investigation(investigator.name, view, handled, truth, weeks, ongoing)
            reports.append(report)
            typer.echo(
                f"{investigator.name}, {view}: root cause {report.known_ok} of {report.known}, "
                f"{report.unsafe} unsafe proposals"
            )
            if report.errors:
                typer.echo(
                    f"warning: {report.errors} of {report.alerts} {investigator.name} "
                    "investigations failed (INVESTIGATION_ERROR); with --live, check the key "
                    "and run again: failed calls are retried, recorded ones are not repeated"
                )
    return reports


def _report(
    world_path: Path,
    name: str,
    names: Sequence[str],
    extra_seeds: Sequence[int] = (),
    scenario_path: Path | None = None,
    held_out: bool = False,
    options: ModelOptions | None = None,
) -> str:
    """Score one world and render its report."""
    options = options or ModelOptions()
    labels_path = world_path.with_suffix(".labels.json")
    if not world_path.exists():
        _fail(f"world not found: {world_path}. Run `revwatch generate` first.")
    if not labels_path.exists():
        _fail(f"labels not found: {labels_path}. Run `revwatch generate` first.")
    other: dict[int, list[DetectionReport]] = {}
    try:
        with Warehouse(world_path) as warehouse:
            days, warmup = warehouse.n_hours // 24, DetectionPolicy().warmup_hours // 24
        if days <= warmup:
            _fail(
                f"the world has {days} days; the first {warmup} are warm-up and are never "
                "scored, so there is nothing to evaluate"
            )
        labels = load_labels(labels_path)
        reports, runs, world_hash, n_hours = _score(world_path, labels, names)
        # Investigation is scored on the alerts of the ML detector.
        agent = _agent(name, options)
        investigated = (
            _investigate(world_path, labels, runs["gbm"], agent, options.concurrency)
            if "gbm" in runs
            else []
        )
        if extra_seeds:
            assert scenario_path is not None
            loaded = load_scenarios(scenario_path)
            with tempfile.TemporaryDirectory() as scratch:
                for seed in extra_seeds:
                    seed_world = Path(scratch) / f"seed-{seed}.duckdb"
                    seed_labels = generate_world(
                        WorldConfig(seed=seed, days=n_hours // 24), loaded, seed_world
                    )
                    other[seed], _, _, _ = _score(seed_world, seed_labels, names, f"seed {seed}: ")
    except (ValueError, StopRun) as exc:
        _fail(str(exc))
    section = render_investigation(investigated, agent_recorded=agent is not None)
    return render_report(reports, name, world_hash, other or None, section, held_out)


@app.command("eval")
def evaluate(
    world: Annotated[str, typer.Option(help="World name (dev, heldout), or all for both.")] = EVERY,
    path: Annotated[Path | None, typer.Option(help="World file to use instead.")] = None,
    detector: Annotated[str, typer.Option(help="all, static, seasonal or gbm.")] = "all",
    seeds: Annotated[
        str,
        typer.Option(help="Also score the dev scenarios on these seeds, e.g. 1,2,3."),
    ] = "",
    scenarios: Annotated[
        Path | None, typer.Option(help="Scenario file for --seeds, instead of the world's own.")
    ] = None,
    out: Annotated[
        Path | None,
        typer.Option(
            help="Where to write the report. Default: docs/eval-report.md for a full run "
            "on both worlds; any other run prints its report."
        ),
    ] = None,
    live: Annotated[
        bool, typer.Option("--live", help="Call the model where no recording exists. Costs money.")
    ] = False,
    record: Annotated[
        bool, typer.Option("--record", help="With --live: save the model's responses.")
    ] = False,
    model: Annotated[str, typer.Option(help="Model for the agent.")] = DEFAULT_MODEL,
    effort: Annotated[str, typer.Option(help="Reasoning effort for the agent.")] = DEFAULT_EFFORT,
    concurrency: Annotated[int, typer.Option(help="Agent investigations at a time.")] = 8,
    max_usd: Annotated[float, typer.Option("--max-usd", help=MAX_USD_HELP)] = DEFAULT_MAX_USD,
) -> None:
    """Score detection and investigation against the labelled scenarios; write the report.

    The scripted investigator needs no model. The agent replays recorded
    responses from data/cassettes/ when they exist; without them it is left out
    unless --live is given.
    """
    if record and not live:
        _fail("--record saves live responses; add --live")
    options = ModelOptions(live, record, model, effort, concurrency, max_usd)
    names = list(DETECTORS) if detector == "all" else [detector]
    if any(name not in DETECTORS for name in names):
        _fail(f"unknown detector {detector!r}; choose all, {', '.join(DETECTORS)}")
    try:
        extra_seeds = [int(part) for part in seeds.split(",") if part.strip()]
    except ValueError:
        _fail(f"--seeds must be whole numbers separated by commas, got {seeds!r}")
    if extra_seeds and path is not None and scenarios is None:
        _fail(
            "--seeds rebuilds the world's scenarios on other seeds; with --path, name the "
            "scenario file the world was built from with --scenarios"
        )
    if path is not None:
        text = _report(path, path.stem, names, extra_seeds, scenarios, options=options)
    elif world == EVERY:
        # Other seeds repeat the development scenarios: that is where everything was tuned.
        seeded = _scenario_path("dev", scenarios) if extra_seeds else None
        text = _report(_world_path("dev"), "dev", names, extra_seeds, seeded, options=options)
        text += "\n" + _report(
            _world_path("heldout"), "heldout", names, held_out=True, options=options
        )
    else:
        seeded = _scenario_path(world, scenarios) if extra_seeds else None
        text = _report(
            _world_path(world), world, names, extra_seeds, seeded, world == "heldout", options
        )
    # The committed report is the full run on both worlds. Anything else (one detector, one
    # world, a world file of your own) must not overwrite it by accident.
    if out is None and detector == "all" and path is None and world == EVERY:
        out = DEFAULT_REPORT
    if options.live:
        typer.echo(options.spent())
    if out is None:
        typer.echo(text)
        typer.echo("report not written; pass --out to keep it")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    typer.echo(f"wrote {out}")


@app.command()
def repeats() -> None:
    """Replay the published recording and every repeat run through today's checks.

    One row per run, both worlds together. It needs no key. The detector finds
    the alerts again, once per world, and that is where the time goes: replaying
    a recording through the checks takes seconds.
    """
    try:
        found = recordings(CASSETTES, REPEATS)
    except ValueError as exc:
        _fail(str(exc))
    replayed: dict[str, dict[str, list[Replayed]]] = {run.label: {} for run in found}
    unreplayable: list[str] = []
    for name in SEEDS:
        world_path = _world_path(name)
        labels_path = world_path.with_suffix(".labels.json")
        if not world_path.exists() or not labels_path.exists():
            _fail(f"world not found: {world_path}. Run `revwatch generate` first.")
        for run in found:
            if not run.path(name).exists():
                _fail(f"{run.label} has no recording for world {name!r}: {run.path(name)}")
        try:
            labels = load_labels(labels_path)
            _, runs, _, _ = _score(world_path, labels, ["gbm"], f"{name}: ")
            views, ongoing = _views(world_path, labels, runs["gbm"])
            for run in found:
                agent = AgentInvestigator(
                    CassetteProvider(run.path(name)), run.model, DEFAULT_EFFORT
                )
                for view, cases, truth in views:
                    handled, lost = replay_each(world_path, cases, agent)
                    replayed[run.label].setdefault(view, []).append(
                        Replayed(handled, truth, ongoing, lost)
                    )
                    if lost:
                        unreplayable.append(f"{run.label}, {name}: {', '.join(lost)}")
                typer.echo(f"{name}: replayed {run.label}")
        except (ValueError, StopRun) as exc:
            _fail(str(exc))
    typer.echo("")
    typer.echo(
        table(
            [
                summarise(
                    run, replayed[run.label]["end to end"], replayed[run.label]["in isolation"]
                )
                for run in found
            ]
        )
    )
    if unreplayable:
        typer.echo("")
        typer.echo(
            "Investigations whose recording no longer matches what the tools return. They "
            "are in no count above:"
        )
        for line in unreplayable:
            typer.echo(f"  {line}")


@app.command()
def replay(
    world: Annotated[str, typer.Option(help="World name.")] = "dev",
    path: Annotated[Path | None, typer.Option(help="World file to use instead.")] = None,
    detector: Annotated[str, typer.Option(help="static, seasonal or gbm.")] = "gbm",
    investigator: Annotated[str, typer.Option(help="script or agent.")] = "script",
    store: Annotated[Path, typer.Option(help="The review queue (SQLite).")] = DEFAULT_STORE,
    concurrency: Annotated[int, typer.Option(help="Investigations at a time.")] = 8,
    live: Annotated[
        bool, typer.Option("--live", help="Call the model where no recording exists. Costs money.")
    ] = False,
    record: Annotated[
        bool, typer.Option("--record", help="With --live: save the model's responses.")
    ] = False,
    model: Annotated[str, typer.Option(help="Model for the agent.")] = DEFAULT_MODEL,
    effort: Annotated[str, typer.Option(help="Reasoning effort for the agent.")] = DEFAULT_EFFORT,
    max_usd: Annotated[float, typer.Option("--max-usd", help=MAX_USD_HELP)] = DEFAULT_MAX_USD,
) -> None:
    """Step through a world: detect, investigate each alert, check it, put it in the queue.

    Incidents a reviewer confirmed in the queue are kept out of the detectors'
    history. Incidents already decided are left as they are.
    """
    if detector not in DETECTORS:
        _fail(f"unknown detector {detector!r}; choose {', '.join(DETECTORS)}")
    if investigator not in INVESTIGATORS:
        _fail(f"unknown investigator {investigator!r}; choose {', '.join(INVESTIGATORS)}")
    if record and not live:
        _fail("--record saves live responses; add --live")
    world_path = path or _world_path(world)
    if not world_path.exists():
        _fail(f"world not found: {world_path}. Run `revwatch generate` first.")
    chosen: Investigator
    options = ModelOptions(live, record, model, effort, concurrency, max_usd)
    if investigator == "script":
        chosen = ScriptInvestigator()
    else:
        name = world if path is None else world_path.stem
        made = _agent(name, options)
        if made is None:
            _fail(
                f"no recorded agent run for world {name!r} and live calls are off. "
                "Run with --live (needs an API key); add --record to save the responses."
            )
        chosen = made
    queue_store = Store(store)
    try:
        with Warehouse(world_path) as warehouse:
            run = run_detection(
                warehouse,
                DETECTORS[detector](),
                confirm=lambda incident: queue_store.confirmed(incident.incident_id),
                progress=typer.echo,
            )
        cases = [Case.of(i) for i in run.incidents if i.opened_tick >= run.scored_from]
        handled = handle(
            world_path,
            cases,
            chosen,
            concurrency=concurrency if investigator == "agent" else 1,
            progress=typer.echo,
        )
        stored, kept = queue(queue_store, world_path, detector, investigator, handled)
    except (ValueError, StopRun) as exc:
        _fail(str(exc))
    proposals = sum(item.assessment.mitigation is not None for item in handled)
    typer.echo(
        f"{len(handled)} alerts: {stored} queued, {kept} already decided and left alone; "
        f"{proposals} carry a proposal that passed every check"
    )
    if live and investigator == "agent":
        typer.echo(options.spent())
    typer.echo(f"queue: {store}")


@app.command()
def stats(
    store: Annotated[Path, typer.Option(help="The review queue (SQLite).")] = DEFAULT_STORE,
) -> None:
    """Numbers from the queue: incidents by severity and cause, override rate, cost."""
    if not store.exists():
        _fail(f"no queue at {store}. Run `revwatch replay` first.")
    numbers = Store(store).stats()
    rate = numbers.pop("override_rate")
    for key, value in numbers.items():
        typer.echo(f"{key}: {json.dumps(value) if isinstance(value, dict) else value}")
    typer.echo("override_rate: " + ("no decisions yet" if rate is None else f"{rate:.0%}"))


@app.command()
def show(
    incident_id: Annotated[str, typer.Argument(help="For example INC-0123456789ab.")],
    store: Annotated[Path, typer.Option(help="The review queue (SQLite).")] = DEFAULT_STORE,
) -> None:
    """One incident: the finding, its evidence, what held it, and every tool call."""
    if not store.exists():
        _fail(f"no queue at {store}. Run `revwatch replay` first.")
    try:
        record = Store(store).get(incident_id)
    except StoreError as exc:
        _fail(str(exc))
    finding = record.finding
    where = ", ".join(f"{k}={v}" for k, v in record.slice.items()) or "all"
    side = "above" if record.direction > 0 else "below"
    typer.echo(f"{record.incident_id}  {record.status}  severity {record.severity}")
    typer.echo(
        f"alert:   {record.metric} {side} expected for {where}, {record.opened_at:%Y-%m-%d %H:%M}"
    )
    typer.echo(
        f"impact:  about EUR {record.impact_eur_per_hour:,.0f} an hour; owner {record.owner}"
    )
    typer.echo(
        f"cause:   {finding.cause} ({finding.confidence} confidence), change {finding.change_id}"
    )
    typer.echo(f"summary: {finding.summary}")
    typer.echo(f"evidence: {record.evidence_ok} of {record.evidence_total} cited values recompute")
    for citation in finding.evidence:
        typer.echo(f"  {citation.call_id} {citation.row} {citation.column} = {citation.value:g}")
    proposed = finding.mitigation
    if record.proposal is not None:
        typer.echo(f"proposal: {record.proposal.action} {json.dumps(record.proposal.target)}")
    elif proposed is not None:
        typer.echo(f"proposal: WITHHELD ({proposed.action} {json.dumps(proposed.target)})")
    else:
        typer.echo("proposal: none")
    for reason in record.reasons:
        typer.echo(f"  held: {reason['code']}: {reason['detail']}")
    typer.echo(f"trace: {record.tool_calls} tool calls by {record.investigator}")
    for call in record.trace:
        outcome = call["error"] or f"{len(call['rows'])} rows"
        typer.echo(f"  {call['call_id']} {call['name']} {json.dumps(call['args'])} -> {outcome}")


@app.command()
def admin(
    world: Annotated[str, typer.Option(help="World whose entities the service knows.")] = "dev",
    path: Annotated[Path | None, typer.Option(help="World file to use instead.")] = None,
    port: Annotated[int, typer.Option(help="Port on localhost.")] = 8765,
    state: Annotated[Path, typer.Option(help="Where the service keeps its state.")] = ADMIN_STATE,
) -> None:
    """Start the mock admin service on localhost, seeded from a world.

    The review page sends approved mitigations here. It needs no secrets.
    """
    world_path = path or _world_path(world)
    if not world_path.exists():
        _fail(f"world not found: {world_path}. Run `revwatch generate` first.")
    service = create_app(state, entities_from_world(world_path))
    typer.echo(f"mock admin service on http://127.0.0.1:{port} (state: {state})")
    uvicorn.run(service, host="127.0.0.1", port=port, log_level="info")
