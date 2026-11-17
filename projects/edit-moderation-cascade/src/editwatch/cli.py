"""The `editwatch` command."""

from __future__ import annotations

import typer

from .config import load_settings
from .store import Store
from .wiki import FeedError, WikiClient

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="A risk-ordered review queue for a live stream of edits.",
)


@app.callback()
def main() -> None:
    """Edit moderation cascade."""


def _client(settings) -> WikiClient:
    return WikiClient(
        settings.wiki_host,
        user_agent=settings.user_agent,
        pause_s=settings.wiki_pause_s,
        risk_pause_s=settings.risk_pause_s,
    )


@app.command()
def backfill() -> None:
    """Fetch three weeks of sampled edit history with outcomes into the local store."""
    from .history import backfill as run_backfill

    settings = load_settings()
    store = Store(settings.db_path)
    client = _client(settings)
    try:
        result = run_backfill(client, store, settings, progress=typer.echo)
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"{result['fetched']} edits fetched, {result['new']} new, {result['skipped']} slices "
        f"skipped, {client.requests['wiki']} requests. Store holds {store.count()} edits."
    )


@app.command()
def record(
    minutes: float = typer.Option(10.0, help="How long to listen."),
    name: str = typer.Option("slice", help="File name under var/recordings/."),
) -> None:
    """Record a slice of the live stream: article edits by people, without account names."""
    from .recorder import record as run_record
    from .stream import StreamReader

    settings = load_settings()
    reader = StreamReader(settings)
    path = settings.recordings_dir / f"{name}.jsonl"
    try:
        written = run_record(reader, path, minutes * 60, progress=typer.echo)
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"{written} edits kept of {reader.events} events in {minutes:g} minutes, "
        f"{reader.reconnects} reconnects. Saved to {path}"
    )


MODELS = {
    "haiku": "claude-haiku-5-5",
    "sonnet": "claude-sonnet-5-5",
    "opus": "claude-opus-5-5",
}


def _provider(
    model: str, cassette, *, live: bool, record: bool, run: int = 1, concurrency: int = 4
):
    """Recorded answers, with the live model behind them when --live is given."""
    from .llm import DEFAULT_EFFORT, AnthropicProvider, CassetteProvider

    params = {"effort": DEFAULT_EFFORT, "max_tokens": 1000}
    inner = None
    if live:
        inner = AnthropicProvider(model=model, max_tokens=1000, concurrency=concurrency)
        params = dict(inner.params)
    if run > 1:
        # A repeat run must not replay the first run's answers.
        params["run"] = run
    return CassetteProvider(cassette, model, inner=inner, record=record, params=params)


def _samples(settings, store):
    """The development and held-out samples, the same every time."""
    from .backtest import sample, windows

    parts = windows(store, settings)
    seeds = settings.sample_seeds
    return {
        "development": sample(parts["development"], settings.sample_size, seeds[0]),
        "held_out": sample(parts["held_out"], settings.sample_size, seeds[1]),
    }


@app.command()
def train() -> None:
    """Train tier 1 on the edits made before the cut and write its summary."""
    import json

    from .backtest import load_tier1, windows

    settings = load_settings()
    store = Store(settings.db_path)
    try:
        tier1 = load_tier1(store, settings, typer.echo)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    parts = windows(store, settings)
    reverted = store.reverted()
    summary = tier1.summary()
    summary["windows"] = {
        name: {"edits": len(edits), "reverted": sum(reverted.get(e.rev_id, False) for e in edits)}
        for name, edits in parts.items()
    }
    path = settings.home / "data" / "tier1.json"
    path.write_text(json.dumps(summary, indent=1) + "\n", encoding="utf-8")
    for name, part in summary["windows"].items():
        typer.echo(f"{name:<12} {part['edits']:>6} edits by people, {part['reverted']:>5} reverted")
    typer.echo(f"Tier 1 trained on {tier1.trained_on} edits. Wrote {path.name}.")


@app.command()
def prepare() -> None:
    """Draw the two model samples and fetch what each of their edits changed."""
    import json

    from .diff import fetch_changes

    settings = load_settings()
    store = Store(settings.db_path)
    samples = _samples(settings, store)
    path = settings.home / "data" / "samples.json"
    path.write_text(
        json.dumps({name: [e.rev_id for e in edits] for name, edits in samples.items()}) + "\n",
        encoding="utf-8",
    )
    client = _client(settings)
    try:
        for name, edits in samples.items():
            typer.echo(f"{name}: {len(edits)} edits")
            fetch_changes(client, store, edits, settings.change_chars, typer.echo)
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _save_latencies(settings, wiki=client.latencies["wiki"])
    typer.echo(f"{client.requests['wiki']} requests. Wrote {path.name}.")


def _save_latencies(settings, **kinds) -> None:
    """Keep measured request times beside the logs, for the load test."""
    import json

    path = settings.home / "data" / "latencies.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    for kind, values in kinds.items():
        if values:
            data[kind] = [round(v, 3) for v in values][-400:]
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")


@app.command()
def baseline(
    part: str = typer.Option("held_out", help="held_out or development."),
) -> None:
    """Ask Wikimedia's own revert-risk model about the sample edits. Resumable."""
    settings = load_settings()
    store = Store(settings.db_path)
    edits = _samples(settings, store)[part]
    have = store.baseline()
    todo = [e for e in edits if e.rev_id not in have]
    client = _client(settings)
    typer.echo(f"{part}: {len(todo)} of {len(edits)} edits to ask about.")
    try:
        for done, edit in enumerate(todo, start=1):
            store.set_baseline(edit.rev_id, client.revert_risk(edit.rev_id))
            if done % 100 == 0 or done == len(todo):
                typer.echo(f"{done} of {len(todo)} scored")
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    _save_latencies(settings, risk=client.latencies["risk"])


def _backtest_paths(settings):
    return settings.log_dir / "backtest.jsonl.gz", settings.cassette_dir / "backtest.jsonl"


@app.command()
def backtest(
    models: str = typer.Option("", help="Comma-separated: haiku, sonnet, opus. Empty: none."),
    every: str = typer.Option("", help="A model to run on every sample edit, e.g. haiku."),
    part: str = typer.Option("both", help="development, held_out or both."),
    share: float = typer.Option(
        None, help="Share of edits the models read (default: model_share)."
    ),
    live: bool = typer.Option(False, help="Call the model for answers not recorded yet."),
    record: bool = typer.Option(False, help="Save the model's answers for replay."),
    run: int = typer.Option(1, help="Repeat run number. Above 1 asks the model afresh."),
    concurrency: int = typer.Option(4, help="Model calls in flight at once."),
    cap: float = typer.Option(1.0, help="Most each model may spend in this command, in US$."),
) -> None:
    """Replay the test week through the cascade and log the answers."""
    from .backtest import (
        ask_model,
        fit_combiner,
        load_tier1,
        windows,
        write_baseline_rows,
        write_rows,
    )
    from .cascade import Cascade
    from .combine import load as load_combiners
    from .combine import save as save_combiners
    from .log import PredictionLog
    from .tier2 import Tier2

    settings = load_settings()
    store = Store(settings.db_path)
    log_path, cassette = _backtest_paths(settings)
    log = PredictionLog(log_path)
    tier1 = load_tier1(store, settings, typer.echo)
    parts = windows(store, settings)
    samples = _samples(settings, store)
    reverted = store.reverted()
    test = parts["development"] + parts["held_out"]
    scores = dict(zip((e.rev_id for e in test), tier1.scores(test), strict=True))
    scores = {rev: float(s) for rev, s in scores.items()}
    _write_outcomes(settings, test, reverted)
    plain = Cascade(settings, tier1)
    written = write_rows(plain, "tier1", store, test, scores, None, log)
    typer.echo(f"tier1: {written} rows for {len(test)} test edits")
    if part not in ("development", "held_out", "both"):
        typer.echo("--part must be development, held_out or both.", err=True)
        raise typer.Exit(2)
    sample_edits = samples["development"] + samples["held_out"] if part == "both" else samples[part]
    written = write_baseline_rows(
        plain, samples["development"] + samples["held_out"], scores, store.baseline(), log
    )
    typer.echo(f"wikimedia: {written} rows")

    combiner_path = settings.home / "data" / "combiners.json"
    combiners = load_combiners(combiner_path)
    slice_share = settings.model_share if share is None else share
    wanted = [(name, slice_share) for name in models.split(",") if name.strip()]
    if every.strip():
        wanted.append((every.strip(), 1.0))
    for name, share in wanted:
        model = MODELS[name.strip()]
        config = f"every-{name}" if share == 1.0 else f"cascade-{name}"
        provider = _provider(
            model, cassette, live=live, record=record, run=run, concurrency=concurrency
        )
        tier2 = Tier2(provider)
        asker = Cascade(settings, tier1, model=model, share=share)
        answers = ask_model(
            asker, tier2, store, sample_edits, scores, concurrency=concurrency, cap_usd=cap,
            progress=typer.echo,
        )  # fmt: skip
        if config not in combiners:
            if part == "held_out":
                typer.echo(f"{config}: run the development days first, to fit its weights.")
                raise typer.Exit(2)
            # Fitted once, on the development sample of the first run, then fixed.
            combiners[config] = fit_combiner(samples["development"], scores, answers, reverted)
            save_combiners(combiners, combiner_path)
        cascade = Cascade(settings, tier1, model=model, combiner=combiners[config], share=share)
        extra = f"#run{run}" if run > 1 else ""
        written = write_rows(
            cascade, config, store, sample_edits, scores, answers, log, version_extra=extra
        )
        _write_meta(settings, cascade.version(config + extra), config, model, run, share)
        typer.echo(
            f"{config}: {written} rows, {tier2.calls} answers, {tier2.errors} failed, "
            f"US${tier2.cost_usd:.3f} spent in this run"
        )


def _write_meta(settings, version: str, config: str, model: str, run: int, share: float) -> None:
    import json

    path = settings.log_dir / "backtest-meta.json"
    meta = json.loads(path.read_text()) if path.exists() else {"versions": {}}
    meta["versions"][version] = {"config": config, "model": model, "run": run, "share": share}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _write_outcomes(settings, edits, reverted) -> None:
    """The outcome of every test edit, beside the log, so scoring needs no store."""
    import gzip
    import json

    path = settings.log_dir / "outcomes-backtest.json.gz"
    payload = {
        "reverted": sorted(e.rev_id for e in edits if reverted.get(e.rev_id) is True),
        "kept": sorted(e.rev_id for e in edits if reverted.get(e.rev_id) is False),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.GzipFile(path, "wb", mtime=0) as handle:
        handle.write(json.dumps(payload).encode("utf-8"))


@app.command("eval")
def evaluate(
    write_report: bool = typer.Option(
        True, "--write/--no-write", help="Write docs/eval-report.md."
    ),
) -> None:
    """Score the committed logs and write the evaluation report. Needs no key and no network."""
    from .report import collect, name_of, render, share

    settings = load_settings()
    data = collect(settings)
    for title, part in (("Held-out sample", "held_out"), ("Development sample", "development")):
        typer.echo(
            f"{title}: {data.get(f'{part}_n', 0)} edits, {data.get(f'{part}_reverted', 0)} reverted"
        )
        for s in data.get(part, []):
            typer.echo(
                f"  {name_of(s, data['meta']):<44} top 2% {share(s.hits[0.02], s.reverted):<17}"
                f" top 5% {share(s.hits[0.05], s.reverted):<17} top 10% "
                f"{share(s.hits[0.10], s.reverted):<17} bottom half {s.bottom_half:>3}"
                f"  US${s.cost_per_1000:.2f}/1,000"
            )
    if write_report:
        path = settings.home / "docs" / "eval-report.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(render(data), encoding="utf-8")
        typer.echo(f"Wrote {path.relative_to(settings.home)}")


def _latencies(settings):
    """Request and model times measured in this project, for the load test.

    Wiki request times for full batches are saved by `prepare`, those for the few
    edits a live request carries by `run`. Model times are read from the recorded
    answers of the model the live run uses. Where nothing was measured, a default.
    """
    import json

    from .loadtest import Latencies

    path = settings.home / "data" / "latencies.json"
    data = json.loads(path.read_text()) if path.exists() else {}
    cassette = settings.cassette_dir / "backtest.jsonl"
    if "model" not in data and cassette.exists():
        times = []
        for line in cassette.read_text().splitlines():
            stats = json.loads(line)["stats"]
            if stats["model"] == settings.live_model:
                times.append(stats["latency_s"])
        if times:
            # Every fifth, so the file stays small and the spread is kept.
            data["model"] = sorted(times)[::5]
            path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    known = Latencies()
    return Latencies(
        tier1=data.get("tier1", known.tier1),
        fetch=data.get("wiki") or known.fetch,
        model=data.get("model") or known.model,
        fetch_small=data.get("wiki_small") or [],
    )


@app.command()
def loadtest(
    recording: str = typer.Option(
        None, help="A recording under var/recordings/ (default: the committed slice)."
    ),
    speeds: str = typer.Option("1,2,5,10,20,50", help="Replay speeds to try."),
    scores_from: str = typer.Option(
        None,
        help="A local recording of the same slice with its summaries, to score it from. "
        "The committed slice has none, and tier 1 reads the summary's shape.",
    ),
    every: bool = typer.Option(
        False, "--every", help="The model reads every edit. Writes loadtest-every.json."
    ),
) -> None:
    """Replay a recorded slice of the stream faster and faster, on a virtual clock."""
    import json

    from .demo import fixture_tier1
    from .loadtest import sustained, sweep
    from .recorder import arrival_offsets, load

    settings = load_settings()
    path = (
        settings.recordings_dir / f"{recording}.jsonl"
        if recording
        else settings.fixture_dir / "stream-slice.jsonl"
    )
    edits = load(path)
    scored = load(settings.recordings_dir / f"{scores_from}.jsonl") if scores_from else edits
    if [e.rev_id for e in scored] != [e.rev_id for e in edits]:
        typer.echo("--scores-from must be a recording of the same edits.", err=True)
        raise typer.Exit(2)
    scores, threshold = _slice_scores(settings, scored, recording is None, fixture_tier1)
    if every:
        threshold = -1.0
    offsets = arrival_offsets(edits)
    results = sweep(
        offsets, scores, tuple(float(s) for s in speeds.split(",")),
        threshold=threshold, queue_bound=settings.queue_bound,
        workers=settings.workers, wiki_pause_s=settings.wiki_pause_s,
        latencies=_latencies(settings),
    )  # fmt: skip
    best = sustained(results, settings.latency_target_s)
    payload = {
        "edits": len(edits),
        "minutes": round(offsets[-1] / 60, 1) if offsets else 0,
        "target_s": settings.latency_target_s,
        "replays": results[0].runs if results else 0,
        "workers": settings.workers,
        "queue_bound": settings.queue_bound,
        "results": [r.summary() for r in results],
        "sustained": (
            f"{best.speed:g}x, {best.rate:.1f} edits a second"
            if best
            else "none of the speeds tried"
        ),
    }
    out = settings.home / "data" / ("loadtest-every.json" if every else "loadtest.json")
    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    for r in payload["results"]:
        typer.echo(
            f"{r['speed']:>4g}x  {r['edits_per_second']:>6} edits/s  "
            f"model read {r['read_by_model']:>4}  skipped {r['skipped_queue_full']:>4}  "
            f"p50 {r['model_p50_s']}  p95 {r['model_p95_s']}"
        )
    typer.echo(
        f"Sustained inside {settings.latency_target_s:g} s: {payload['sustained']}. "
        f"Wrote {out.name}."
    )


def _slice_scores(settings, edits, committed_slice: bool, fixture_tier1):
    """Tier-1 scores of a recording's edits, and the score that marks the model's slice.

    For the committed slice the scores of the tier 1 trained on real history are kept
    beside it, so the load test gives the same numbers on a machine without the store.
    """
    import json

    from .backtest import load_tier1

    saved = settings.fixture_dir / "stream-slice-scores.json"
    if settings.db_path.exists():
        try:
            tier1 = load_tier1(Store(settings.db_path), settings)
            scores = [round(float(x), 5) for x in tier1.scores(edits)]
            threshold = tier1.threshold(settings.model_share)
            if committed_slice:
                saved.write_text(json.dumps({"threshold": threshold, "scores": scores}) + "\n")
            return scores, threshold
        except ValueError:
            pass
    if committed_slice and saved.exists():
        data = json.loads(saved.read_text())
        return data["scores"], data["threshold"]
    tier1 = fixture_tier1(settings)
    return [float(x) for x in tier1.scores(edits)], tier1.threshold(settings.model_share)


def _tier1_or_fixture(settings, fixture_tier1):
    """The tier 1 trained on real history if the store has it, else on the invented fixture."""
    from .backtest import load_tier1

    if settings.db_path.exists():
        try:
            return load_tier1(Store(settings.db_path), settings)
        except ValueError:
            pass
    return fixture_tier1(settings)


def _live_cascade(settings, store, *, model: bool):
    """Tier 1 trained on all stored history, and the live model with its fitted weights."""
    import pickle

    from .cascade import Cascade
    from .combine import DEFAULT
    from .combine import load as load_combiners
    from .llm import resolve_api_key
    from .tier1 import train
    from .tier2 import Tier2

    history = store.edits(source="history")
    if not history:
        raise ValueError("No stored history. Run `editwatch backfill` first.")
    path = settings.home / "var" / f"tier1-live-{len(history)}-{history[-1].rev_id}.pkl"
    if path.exists():
        with open(path, "rb") as handle:
            tier1 = pickle.load(handle)
    else:
        typer.echo(f"training the live tier 1 on {len(history)} edits")
        tier1 = train(history, store.reverted())
        with open(path, "wb") as handle:
            pickle.dump(tier1, handle)
    short = next((k for k, v in MODELS.items() if v == settings.live_model), None)
    if short is None:
        raise ValueError(f"live_model must be one of: {', '.join(MODELS.values())}.")
    combiners = load_combiners(settings.home / "data" / "combiners.json")
    combiner = combiners.get(f"cascade-{short}")
    if combiner is None:
        typer.echo(f"No fitted weights for {short} in data/combiners.json: using the defaults.")
        combiner = DEFAULT
    tier2 = None
    if model and resolve_api_key():
        provider = _provider(
            settings.live_model, settings.home / "var" / "cassettes" / "live.jsonl", live=True,
            record=True, concurrency=settings.workers,
        )  # fmt: skip
        tier2 = Tier2(provider)
    elif model:
        typer.echo("No model key found: running tier 1 alone.")
    if tier2 is None:
        # A run without the model is a different system and gets its own line.
        return Cascade(settings, tier1), None
    return Cascade(settings, tier1, model=settings.live_model, combiner=combiner), tier2


@app.command()
def run(
    minutes: float = typer.Option(10.0, help="How long to read the stream."),
    model: bool = typer.Option(True, help="--no-model runs tier 1 alone."),
) -> None:
    """Read the live stream for a window, order the queue and log every answer."""
    import threading
    import time
    from datetime import UTC, datetime

    from .budget import PacedBudget
    from .diff import fetch_changes
    from .live import append_run, live_paths, run_window, spent_today
    from .loadtest import SMALL_BATCH
    from .log import PredictionLog
    from .stream import StreamReader

    settings = load_settings()
    store = Store(settings.db_path)
    log = PredictionLog(live_paths(settings)["log"])
    try:
        cascade, tier2 = _live_cascade(settings, store, model=model)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    budget = PacedBudget(settings.daily_cap_usd, spent_today(log, datetime.now(UTC)))
    client = _client(settings)
    reader = StreamReader(settings)
    deadline = time.monotonic() + minutes * 60

    own = threading.local()
    small_requests: list[float] = []

    def fetch(edits):
        # Runs on the loop's fetch thread. A SQLite connection belongs to the thread
        # that opened it, so this thread opens its own.
        if not hasattr(own, "store"):
            own.store = Store(settings.db_path)
        began = time.monotonic()
        fetch_changes(client, own.store, edits, settings.change_chars)
        if len(edits) <= SMALL_BATCH:
            small_requests.append(time.monotonic() - began)
        return {e.rev_id: own.store.change(e.rev_id) for e in edits}

    try:
        summary = run_window(
            cascade, reader.edits(stop=lambda: time.monotonic() >= deadline), tier2=tier2,
            fetch=fetch, log=log, budget=budget,
            config="cascade" if tier2 is not None else "tier1-live",
            on_edit=lambda edit: store.add_edits([(edit, [])], source="live"),
        )  # fmt: skip
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    summary.update(events=reader.events, reconnects=reader.reconnects, minutes=minutes)
    append_run(settings, summary)
    # How long a request for a few edits took, for the load test at the stream's rate.
    _save_latencies(settings, wiki_small=small_requests)
    typer.echo(
        f"{summary['edits']} edits in {minutes:g} minutes, {summary['read_by_model']} read by "
        f"the model, skipped {summary['skipped_queue_full']} (queue) and "
        f"{summary['skipped_budget']} (budget), US${summary['cost_usd']:.4f}. Seconds to the "
        f"queue for the model tier: median {summary['model_p50_s']}, "
        f"95th percentile {summary['model_p95_s']}."
    )
    if summary["model_errors"]:
        typer.echo(f"{summary['model_errors']} model calls gave no usable answer.", err=True)


@app.command()
def replay(
    speed: float = typer.Option(10.0, help="How many times faster than recorded."),
    recording: str = typer.Option(None, help="A recording under var/recordings/."),
) -> None:
    """Play a recording through the live loop, faster than it happened. No network."""
    from .cascade import Cascade
    from .demo import fixture_tier1
    from .live import run_window
    from .log import PredictionLog
    from .models import Change
    from .recorder import load
    from .recorder import replay as play

    settings = load_settings()
    path = (
        settings.recordings_dir / f"{recording}.jsonl"
        if recording
        else settings.fixture_dir / "stream-slice.jsonl"
    )
    edits = load(path)
    tier1 = _tier1_or_fixture(settings, fixture_tier1)
    cascade = Cascade(settings, tier1)
    out = settings.home / "var" / "replay.jsonl"
    out.unlink(missing_ok=True)
    # The recorded event times are hours or days old. Without them the loop counts
    # each edit's wait from the moment the replay delivered it.
    arriving = (edit.model_copy(update={"event_time": None}) for edit in play(edits, speed))
    summary = run_window(
        cascade, arriving, tier2=None,
        fetch=lambda batch: {e.rev_id: Change(rev_id=e.rev_id, available=False) for e in batch},
        log=PredictionLog(out), config="replay",
    )  # fmt: skip
    typer.echo(
        f"{summary['edits']} edits replayed at {speed:g}x in {summary['seconds']} seconds; "
        f"tier 1 placed each within {summary['tier1_p95_s']} seconds of its arrival "
        "(95th percentile)."
    )


@app.command()
def truth() -> None:
    """Read whether each live edit was reverted, once it is 48 hours old."""
    from datetime import UTC, datetime

    from .live import live_paths
    from .log import PredictionLog
    from .truth import OutcomeLog, collect, due

    settings = load_settings()
    paths = live_paths(settings)
    outcomes = OutcomeLog(paths["outcomes"])
    now = datetime.now(UTC)
    waiting = due(PredictionLog(paths["log"]).read(), outcomes.read(), settings, now)
    if not waiting:
        typer.echo("No live edit is 48 hours old without an outcome.")
        return
    try:
        written = outcomes.append(collect(_client(settings), waiting, now, typer.echo))
    except FeedError as exc:
        typer.echo(f"Feed error: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo(f"{written} outcomes written.")


@app.command()
def health() -> None:
    """Listen to the stream for twenty seconds and check it. Exits non-zero on a failure."""
    from .health import run_checks, write
    from .stream import StreamReader

    settings = load_settings()
    result = run_checks(StreamReader(settings), _client(settings))
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
def demo(
    live: bool = typer.Option(False, help="Call the model for answers not recorded yet."),
    record: bool = typer.Option(False, help="Save the model's answers for replay."),
    top: int = typer.Option(12, help="How many queue entries to print."),
) -> None:
    """Order the invented wiki's new edits. Needs no key and no network."""
    from .demo import run_demo
    from .queue import build_queue, format_item
    from .report import share
    from .tier2 import Tier2

    settings = load_settings()
    provider = _provider(
        settings.live_model, settings.cassette_dir / "fixture.jsonl", live=live, record=record
    )
    rows, outcomes, scores = run_demo(settings, Tier2(provider))
    items = build_queue(rows, outcomes)
    typer.echo(f"{len(items)} new edits to an invented wiki, riskiest first.\n")
    for item in items[:top]:
        typer.echo(format_item(item))
        typer.echo("")
    for s in scores:
        typer.echo(
            f"{s.config:<8} reverted edits in the first {s.depth[0.20]} of {s.edits}: "
            f"{share(s.hits[0.20], s.reverted)}; read by the model {s.model_read}; "
            f"in the bottom half {s.bottom_half}"
        )


@app.command()
def queue(
    top: int = typer.Option(10, help="How many entries to show."),
) -> None:
    """Print the riskiest edits of the last live run. Reads the local store."""
    from .live import live_paths, read_runs
    from .log import PredictionLog
    from .queue import build_queue, format_item
    from .truth import OutcomeLog

    settings = load_settings()
    runs = read_runs(settings)
    if not runs or not settings.db_path.exists():
        typer.echo("No live run yet. Run `editwatch run` first.")
        return
    store = Store(settings.db_path)
    paths = live_paths(settings)
    last = [
        p for p in PredictionLog(paths["log"]).read() if p.system_version == runs[-1]["version"]
    ]
    newest = max((p.written_at for p in last), default=None)
    rows = []
    for p in last:
        if newest is not None and (newest - p.written_at).total_seconds() > 3600:
            continue
        edit = store.edit(p.rev_id)
        if edit is not None:
            rows.append((edit, p, store.change(p.rev_id)))
    outcomes = {o.rev_id: o.reverted for o in OutcomeLog(paths["outcomes"]).read().values()}
    for item in build_queue(rows, outcomes)[:top]:
        typer.echo(format_item(item))
        typer.echo("")
