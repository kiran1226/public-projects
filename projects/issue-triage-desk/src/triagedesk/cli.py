"""The `triagedesk` command."""

from __future__ import annotations

from datetime import date, datetime

import typer

from .config import load_settings
from .feed import FeedError, GitHubClient, resolve_token
from .store import Store

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Labels and duplicate candidates for new issues, scored against the maintainers.",
)


@app.callback()
def main() -> None:
    """Issue triage desk."""


def _client(settings, *, max_wait_s: float = 3700.0) -> GitHubClient:
    return GitHubClient(
        settings.repo,
        token=resolve_token(),
        user_agent=settings.user_agent,
        max_wait_s=max_wait_s,
    )


def _date(value: str | None, default: date) -> date:
    return date.fromisoformat(value) if value else default


@app.command()
def backfill(
    start: str = typer.Option(None, "--from", help="First creation day (default: history_start)."),
    end: str = typer.Option(None, "--to", help="Last creation day (default: today)."),
    refresh: bool = typer.Option(
        False, help="Fetch finished months again, to bring labels and state up to date."
    ),
) -> None:
    """Fetch every issue created in a range into the local store. Resumable."""
    from .backfill import fetch_range

    settings = load_settings()
    store = Store(settings.db_path)
    client = _client(settings)
    first = _date(start, settings.history_start)
    last = _date(end, datetime.now().date())
    typer.echo(f"Fetching issues of {settings.repo} created {first} to {last}.")
    try:
        result = fetch_range(client, store, first, last, refresh=refresh, progress=typer.echo)
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"{result['fetched']} issues fetched, {result['new']} new, "
        f"{result['skipped']} finished months skipped, "
        f"{client.requests['search']} search requests. Store holds {store.count()} issues."
    )


@app.command()
def timelines(
    start: str = typer.Option(None, "--from", help="First creation day (default: cut)."),
    end: str = typer.Option(None, "--to", help="Last creation day (default: test_end)."),
    wait: bool = typer.Option(
        True, help="Wait for the hourly quota to reset. --no-wait stops when it runs out."
    ),
) -> None:
    """Fetch the timeline of every stored issue closed as a duplicate in a range.

    The timeline is where maintainers name the original. One request per issue, 60 an
    hour without a token, so this is resumable and can run in the background.
    """
    from datetime import timedelta

    from .config import day_start
    from .truth import fetch_timelines

    settings = load_settings()
    store = Store(settings.db_path)
    first = day_start(_date(start, settings.cut))
    last = day_start(_date(end, settings.test_end)) + timedelta(days=1)
    numbers = [i.number for i in store.issues(first, last) if i.closed_as_duplicate]
    client = _client(settings, max_wait_s=3700.0 if wait else 30.0)
    typer.echo(f"{len(numbers)} issues closed as duplicates, created {first:%Y-%m-%d} onwards.")
    try:
        fetched, missing = fetch_timelines(client, store, numbers, progress=typer.echo)
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{fetched} timelines fetched, {missing} still missing.")


def _embedder(stub: bool):
    from .embed import FastEmbedder, HashEmbedder

    return HashEmbedder() if stub else FastEmbedder()


def _provider(settings, *, cassette: str, live: bool, record: bool, run: int, concurrency: int):
    """Recorded answers, with the live model behind them when --live is given."""
    from .llm import DEFAULT_EFFORT, DEFAULT_MODEL, AnthropicProvider, CassetteProvider

    inner = AnthropicProvider(concurrency=concurrency) if live else None
    model = inner.model if inner else DEFAULT_MODEL
    params = dict(inner.params) if inner else {"effort": DEFAULT_EFFORT, "max_tokens": 4000}
    if run > 1:
        # A repeat run must not replay the first run's answers.
        params["run"] = run
    return CassetteProvider(cassette, model, inner=inner, record=record, params=params)


@app.command()
def init(
    repo: str = typer.Option(None, "--repo", help="owner/name of a public repository."),
) -> None:
    """Set the desk up for a repository: write the configuration and read its labels."""
    import json
    from dataclasses import replace

    import yaml

    from .config import CONFIG_NAME, GroupConfig, to_mapping
    from .taxonomy import detect_groups

    settings = load_settings()
    path = settings.home / CONFIG_NAME
    if repo and repo != settings.repo and path.exists():
        typer.echo(f"{path.name} is set up for {settings.repo}. Move it away to start afresh.")
        raise typer.Exit(1)
    if repo:
        settings = replace(settings, repo=repo)
    client = _client(settings, max_wait_s=30.0)
    try:
        infos = client.labels()
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    store = Store(settings.db_path)
    store.set_state("labels", json.dumps([info.model_dump() for info in infos]))
    typer.echo(f"{settings.repo}: {len(infos)} labels read.")
    if not path.exists():
        groups = detect_groups(infos)
        settings = replace(
            settings,
            groups=tuple(GroupConfig(name, prefix) for name, prefix in groups.items()),
            keywords={},
        )
        path.write_text(yaml.safe_dump(to_mapping(settings), sort_keys=False), encoding="utf-8")
        typer.echo(f"Wrote {path.name} with label groups: {', '.join(groups) or 'none found'}.")
        typer.echo("Check the groups and the dates in it, then run `triagedesk backfill`.")


def _backtest_paths(settings):
    return (
        settings.log_dir / "backtest.jsonl.gz",
        settings.log_dir / "outcomes-backtest.jsonl",
    )


@app.command()
def truth(
    backtest: bool = typer.Option(False, help="Read outcomes for the backtest window."),
) -> None:
    """Record what the maintainers did with issues old enough to have an outcome."""
    import json

    from .backtest import training_issues, window
    from .models import LabelInfo
    from .taxonomy import build_taxonomy, public_form, save
    from .truth import NOT_FETCHED, OutcomeLog, collect

    settings = load_settings()
    store = Store(settings.db_path)
    if not backtest:
        from .live import collect_live_truth

        client = _client(settings, max_wait_s=30.0)
        try:
            result = collect_live_truth(settings, store, client, progress=typer.echo)
        except FeedError as exc:
            typer.echo(f"Feed error: {exc}", err=True)
            raise typer.Exit(1) from exc
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(1) from exc
        typer.echo(json.dumps(result))
        return
    training = training_issues(store, settings, settings.cut)
    raw_labels = store.get_state("labels")
    infos = [LabelInfo.model_validate(i) for i in json.loads(raw_labels)] if raw_labels else []
    taxonomy = build_taxonomy(settings, training, infos)
    save(public_form(taxonomy), settings.home / "data" / "taxonomy.json")
    issues = window(store, settings.cut, settings.test_end)
    outcomes = collect(store, taxonomy, issues)
    log = OutcomeLog(_backtest_paths(settings)[1])
    added = log.append(outcomes)
    waiting = sum(1 for o in log.read().values() if o.original_status == NOT_FETCHED)
    typer.echo(
        f"{len(outcomes)} issues in the window, {added} outcomes written, "
        f"{waiting} duplicates still wait for their timeline."
    )


@app.command()
def tune() -> None:
    """Print the tables the search mode and the similarity thresholds were chosen from."""
    from .backtest import window
    from .embed import FastEmbedder
    from .evals import families_from
    from .index import IssueIndex
    from .truth import OutcomeLog
    from .tune import retrieval_table, similarity_grid

    settings = load_settings()
    store = Store(settings.db_path)
    index = IssueIndex(store, FastEmbedder())
    index.embed_missing(typer.echo)
    index.load()
    outcomes = OutcomeLog(_backtest_paths(settings)[1]).read()
    families = families_from(outcomes)
    from datetime import timedelta

    issues = window(store, settings.cut, settings.held_out_start - timedelta(days=1))
    table = retrieval_table(
        index, issues, outcomes, families, settings.candidates, settings.fusion_k
    )
    typer.echo(f"Development months: {len(issues)} issues, {table['duplicates']} duplicates.")
    typer.echo("Original among the first k earlier issues:")
    for mode, hits in table["modes"].items():
        typer.echo(f"  {mode:<7}" + "".join(f"  top {k}: {v:>3}" for k, v in hits.items()))
    typer.echo(f"Similarity thresholds ({settings.retrieval} search):")
    for row in similarity_grid(
        index, issues, outcomes, families, settings.retrieval, settings.candidates,
        settings.fusion_k,
    ):  # fmt: skip
        typer.echo(
            f"  {row['threshold']:.2f}  flagged {row['flagged']:>4}  right {row['right']:>3}  "
            f"new issues flagged {row['new_issues']:>4}"
        )


@app.command()
def replay(
    start: str = typer.Option(None, "--from", help="First creation day (default: cut)."),
    end: str = typer.Option(None, "--to", help="Last creation day (default: test_end)."),
    configs: str = typer.Option("rules,classifier", help="Comma-separated configurations."),
    live: bool = typer.Option(False, help="Call the model for answers not recorded yet."),
    record: bool = typer.Option(False, help="Save the model's answers for replay."),
    run: int = typer.Option(1, help="Repeat run number. Above 1 asks the model afresh."),
    concurrency: int = typer.Option(4, help="Model calls in flight at once."),
    cap: float = typer.Option(1.0, help="Most this command may spend on the model, in US$."),
    stub_embedder: bool = typer.Option(False, help="Use the test embedder (no download)."),
) -> None:
    """Run stored issues through the desk in date order and log the answers."""
    from .backtest import build_desk, prefetch, window
    from .backtest import replay as run_replay
    from .budget import Budget
    from .log import PredictionLog
    from .pipeline import PROMPT_OF

    settings = load_settings()
    store = Store(settings.db_path)
    wanted = tuple(name.strip() for name in configs.split(",") if name.strip())
    needs_model = any(name in PROMPT_OF for name in wanted)
    provider = None
    budget = Budget(cap)
    if needs_model:
        cassette = settings.local_cassette_dir / "backtest.jsonl"
        provider = _provider(
            settings, cassette=cassette, live=live, record=record, run=run,
            concurrency=concurrency,
        )  # fmt: skip
    desk = build_desk(
        settings, store, _embedder(stub_embedder), cut=settings.cut, provider=provider,
        budget=budget, progress=typer.echo,
    )  # fmt: skip
    issues = window(store, _date(start, settings.cut), _date(end, settings.test_end))
    typer.echo(f"{len(issues)} issues, configurations: {', '.join(wanted)}.")
    if needs_model and live:
        for variant in dict.fromkeys(PROMPT_OF[name] for name in wanted if name in PROMPT_OF):
            prefetch(desk, issues, variant, concurrency=concurrency, progress=typer.echo)
    result = run_replay(
        desk, issues, wanted, PredictionLog(_backtest_paths(settings)[0]), progress=typer.echo
    )
    _write_meta(settings, store, desk, wanted, run)
    typer.echo(
        f"{result['written']} predictions written, {result['skipped']} already in the log. "
        f"Model spend in this run: US${budget.spent_usd:.3f}"
        + (f" ({budget.refused} calls refused at the cap)." if budget.refused else ".")
    )


def _write_meta(settings, store, desk, configs, run: int) -> None:
    """Facts about the backtest that the report quotes, kept beside the log."""
    import json

    from .pipeline import PROMPT_OF

    path = settings.log_dir / "backtest-meta.json"
    meta = json.loads(path.read_text("utf-8")) if path.exists() else {}
    thresholds = desk.classifier.thresholds
    newest = max((store.refreshed_at(i.number) for i in store.issues()[-50:]), default=None)
    meta.update(
        {
            "repo": settings.repo,
            "stored_issues": store.count(),
            "training_issues": desk.classifier.trained_on,
            "cut": settings.cut.isoformat(),
            "truth_read": newest.date().isoformat() if newest else None,
            "embedder": desk.index.embedder.name,
            "retrieval": settings.retrieval,
            "classifier": {
                "propose": thresholds.propose,
                "confident": thresholds.confident,
                "validation": thresholds.validation,
            },
        }
    )
    versions = meta.setdefault("versions", {})
    for config in configs:
        entry = {"config": config, "run": run}
        if config in PROMPT_OF and PROMPT_OF[config] in desk.triagers:
            entry["model"] = desk.triagers[PROMPT_OF[config]].provider.model
        versions[desk.version(config)] = entry
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")


@app.command("eval")
def evaluate(
    write_report: bool = typer.Option(
        True, "--write/--no-write", help="Write docs/eval-report.md."
    ),
) -> None:
    """Score the committed logs and write the evaluation report. Needs no key and no network."""
    from .report import collect, config_name, render, share

    settings = load_settings()
    data = collect(settings)
    for title, key in (("Held-out months", "held_out"), ("Development months", "development")):
        typer.echo(f"{title}: {data[f'{key}_truth']['issues']} issues")
        for s in data[key]:
            every = s.all_labels()
            typer.echo(
                f"  {config_name(s, data['meta']):<52} labels right "
                f"{share(every.right, every.proposed):<20} found "
                f"{share(every.right, every.present):<20} area right "
                f"{share(s.primary_right, s.labelled):<20} dup right {s.flagged_right:>3} "
                f"false {s.false_duplicates:>3}  alone {s.alone:>4} clean {s.alone_clean:>4}"
            )
    if write_report:
        path = settings.home / "docs" / "eval-report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(data), encoding="utf-8")
        typer.echo(f"Wrote {path.relative_to(settings.home)}")


@app.command()
def index(
    stub_embedder: bool = typer.Option(False, help="Use the test embedder (no download)."),
) -> None:
    """Embed every stored issue that has no vector yet. Resumable."""
    from .index import IssueIndex

    settings = load_settings()
    store = Store(settings.db_path)
    added = IssueIndex(store, _embedder(stub_embedder)).embed_missing(typer.echo)
    typer.echo(f"{added} issues embedded. Store holds {store.count()} issues.")


def _live_taxonomy_path(settings):
    return settings.home / "data" / "taxonomy-live.json"


@app.command()
def run(
    model: bool = typer.Option(True, help="--no-model runs the key-free configurations only."),
    apply: bool = typer.Option(
        False, help="Write labels and one comment to the tracker. Needs apply_allow and a token."
    ),
    stub_embedder: bool = typer.Option(False, help="Use the test embedder (no download)."),
) -> None:
    """Answer the issues opened since the last run. This is what the schedule calls.

    Shadow mode by default: the prediction log is the only thing written.
    """
    from datetime import UTC

    from .backtest import build_desk
    from .budget import Budget
    from .live import live_paths, run_live, spent_today
    from .llm import resolve_api_key
    from .log import PredictionLog
    from .taxonomy import public_form, save

    settings = load_settings()
    store = Store(settings.db_path)
    now = datetime.now(UTC)
    spent = spent_today(PredictionLog(live_paths(settings)["log"]), now)
    budget = Budget(settings.daily_cap_usd, spent)
    provider = None
    if model and resolve_api_key():
        provider = _provider(
            settings, cassette=settings.local_cassette_dir / "live.jsonl", live=True,
            record=True, run=1, concurrency=2,
        )  # fmt: skip
    elif model:
        typer.echo("No model key found: running the key-free configurations only.")
    cut = settings.live_cut or settings.cut
    writer = None
    if apply:
        from .act import NotAllowed, Writer

        try:
            writer = Writer(settings, resolve_token())
        except NotAllowed as exc:
            typer.echo(f"Refusing to apply: {exc}", err=True)
            raise typer.Exit(2) from exc
    try:
        desk = build_desk(
            settings, store, _embedder(stub_embedder), cut=cut, provider=provider,
            budget=budget, progress=typer.echo,
        )  # fmt: skip
        save(public_form(desk.taxonomy), _live_taxonomy_path(settings))
        summary = run_live(
            desk, _client(settings, max_wait_s=90.0), now=now, budget=budget, writer=writer,
            progress=typer.echo,
        )  # fmt: skip
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if summary["model_errors"]:
        typer.echo(
            f"{summary['model_errors']} model calls failed (check the key). Those issues "
            "were answered by the classifier and sent to a person.",
            err=True,
        )
    if summary["calls_refused_at_cap"]:
        typer.echo(
            f"The daily cap of US${settings.daily_cap_usd:.2f} was reached: "
            f"{summary['calls_refused_at_cap']} issues were answered without the model."
        )


@app.command()
def health() -> None:
    """Check the feed. Exits with an error if a check fails, so a schedule notices."""
    from .health import run_checks, write
    from .models import Taxonomy
    from .taxonomy import load

    settings = load_settings()
    store = Store(settings.db_path)
    path = _live_taxonomy_path(settings)
    if not path.exists():
        path = settings.home / "data" / "taxonomy.json"
    taxonomy = load(path) if path.exists() else Taxonomy(repo=settings.repo)
    result = run_checks(settings, store, _client(settings, max_wait_s=90.0), taxonomy)
    write(result, settings.scoreboard_dir / "health.json")
    for check in result["checks"]:
        typer.echo(f"{'ok  ' if check['ok'] else 'FAIL'} {check['name']}: {check['detail']}")
    if not result["ok"]:
        raise typer.Exit(1)


@app.command()
def scoreboard() -> None:
    """Write scoreboard.json and the static page from the logs."""
    from .health import read
    from .scoreboard import write

    settings = load_settings()
    page = write(settings, health=read(settings.scoreboard_dir / "health.json"))
    typer.echo(f"Wrote {page}")


@app.command()
def queue(
    limit: int = typer.Option(10, help="How many issues to show, newest first."),
    config: str = typer.Option("model", help="Whose answers to show."),
) -> None:
    """Print the latest live proposals beside their issues. Reads the local store."""
    from .live import live_paths
    from .log import PredictionLog
    from .queue import build_queue, format_item
    from .truth import OutcomeLog

    settings = load_settings()
    store = Store(settings.db_path)
    paths = live_paths(settings)
    predictions = PredictionLog(paths["log"]).read()
    if not any(p.config == config for p in predictions):
        config = "classifier"
    items = build_queue(
        store, predictions, OutcomeLog(paths["outcomes"]).read(), config=config, limit=limit
    )
    if not items:
        typer.echo("No live proposals yet. Run `triagedesk run` first.")
        return
    for item in items:
        typer.echo(format_item(item))
        typer.echo("")


@app.command()
def demo(
    live: bool = typer.Option(False, help="Call the model for answers not recorded yet."),
    record: bool = typer.Option(False, help="Save the model's answers for replay."),
    stub_embedder: bool = typer.Option(False, help="Use the test embedder (no download)."),
) -> None:
    """Run the desk on the invented tracker in data/fixture. Needs no key and no network."""
    from .demo import fixture_desk, fixture_settings, run_demo
    from .queue import format_item
    from .report import config_name, share

    settings = fixture_settings(load_settings().home)
    provider = _provider(
        settings, cassette=settings.cassette_dir / "fixture.jsonl", live=live, record=record,
        run=1, concurrency=4,
    )  # fmt: skip
    desk = fixture_desk(settings, _embedder(stub_embedder), provider)
    _, outcomes, items, scores = run_demo(desk, ("rules", "classifier", "model"))
    typer.echo(
        f"{desk.classifier.trained_on} invented issues of a fictional tracker for training, "
        f"{len(outcomes)} new arrivals.\n"
    )
    for item in sorted(items, key=lambda i: i.issue.number):
        typer.echo(format_item(item))
        typer.echo("")
    for s in scores:
        every = s.all_labels()
        typer.echo(
            f"{config_name(s, {}):<45} labels right {share(every.right, every.proposed):<18} "
            f"duplicates found {s.flagged_right} of {s.duplicates}, "
            f"new issues called duplicates {s.false_duplicates}, to a person {s.person}"
        )
