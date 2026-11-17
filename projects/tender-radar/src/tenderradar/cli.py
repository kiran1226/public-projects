"""Command-line entry point. Every command the scheduled job runs is here."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import typer

from . import backtest as bt
from . import demo as demo_module
from . import report, scoreboard
from .assess import SYSTEM_PROMPT, Assessor
from .backfill import fetch_range
from .budget import Budget
from .config import CUT_DATE, HELD_OUT_START, PROFILE_START, TEST_END, Settings, load_settings
from .embed import HashEmbedder
from .feed import FeedError, TedClient
from .health import assess_health
from .live import DayIndex, export_aliases, live_outcomes, run_live
from .llm import DEFAULT_EFFORT, DEFAULT_MODEL, AnthropicProvider, CassetteProvider, resolve_api_key
from .log import PredictionLog
from .panel import MIN_PAST_WINS, MIN_TEST_WINS
from .pipeline import process_day
from .profile import build_profile, load_client_profile, load_profiles, save_profiles
from .store import AWARD, COMPETITION, Store
from .truth import OutcomeLog

app = typer.Typer(add_completion=False, no_args_is_help=True)

PANEL_FILE = "panel.jsonl.gz"
ALIASES_FILE = "aliases.json"
HEALTH_FILE = "health.json"


@app.callback()
def main() -> None:
    """Tender radar: a daily shortlist of EU public tenders, scored against who won."""


def _day(value: str | None, default: date) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date() if value else default


def _provider(settings: Settings, cassette: str, *, live: bool, record: bool) -> CassetteProvider:
    """Recorded responses first; the live model only when asked for."""
    inner = AnthropicProvider() if live else None
    params = inner.params if inner else {"effort": DEFAULT_EFFORT, "max_tokens": 4000}
    model = inner.model if inner else DEFAULT_MODEL
    return CassetteProvider(
        settings.cassette_dir / cassette, model=model, inner=inner, record=record, params=params
    )


@app.command()
def backfill(
    kind: str = typer.Option("both", help="competition, award or both"),
    start: str = typer.Option(None, "--from", help="First publication day, YYYY-MM-DD"),
    end: str = typer.Option(None, "--to", help="Last publication day, YYYY-MM-DD"),
    force: bool = typer.Option(False, help="Fetch months again even if already complete"),
) -> None:
    """Download notices for a date range into the local store. Free, no key needed."""
    settings = load_settings()
    store = Store(settings.db_path)
    client = TedClient(pause_s=settings.feed_pause_s, user_agent=settings.user_agent)
    first, last = _day(start, PROFILE_START), _day(end, date.today())
    kinds = [COMPETITION, AWARD] if kind == "both" else [kind]
    try:
        for one in kinds:
            total = fetch_range(
                client,
                store,
                one,
                settings.divisions,
                first,
                last,
                force=force,
                progress=typer.echo,
            )
            typer.echo(f"{one}: fetched {total}, {store.count(one)} in the store")
    finally:
        typer.echo(f"requests made: {client.requests_made}")
        client.close()
        store.close()


@app.command()
def replay(
    start: str = typer.Option(None, "--from", help="First test day, default the cut date"),
    end: str = typer.Option(None, "--to", help="Last test day, default the end of the window"),
    panel_size: int = typer.Option(200, help="Suppliers on the panel"),
    fresh: bool = typer.Option(False, help="Delete the backtest log first"),
    model_profiles: int = typer.Option(0, help="Run the model for this many panel suppliers"),
    model_days: int = typer.Option(3, help="Days per supplier the model is run on"),
    live: bool = typer.Option(
        False, "--live", help="Allow paid model calls for missing recordings"
    ),
    record: bool = typer.Option(False, help="Save model responses to the cassette"),
    budget_usd: float = typer.Option(1.0, help="Most this command may spend on the model"),
    concurrency: int = typer.Option(8, help="Model calls in flight at once"),
) -> None:
    """Replay stored history through the pipeline and write the backtest log."""
    settings = load_settings()
    log_path = settings.log_dir / report.BACKTEST_LOG
    outcome_path = settings.log_dir / report.BACKTEST_OUTCOMES
    if fresh:
        for path in (log_path, outcome_path, settings.log_dir / report.BACKTEST_META):
            path.unlink(missing_ok=True)
    store = Store(settings.db_path)
    world = bt.load_world(store, panel_size=panel_size, progress=typer.echo)
    first, last = _day(start, CUT_DATE), _day(end, TEST_END)
    embedder = bt.make_embedder(settings)
    rankers = bt.build_rankers(world.history_texts, embedder)
    provider = _provider(settings, "backtest.jsonl", live=live, record=record)
    versions = bt.ranker_versions(
        settings,
        embedder.name,
        model=provider.model,
        effort=str(provider.params.get("effort")),
        prompt=SYSTEM_PROMPT,
    )
    days = {d: n for d, n in world.notices_by_day().items() if first <= d <= last}
    typer.echo(
        f"embedding {sum(len(n) for n in days.values())} tenders (cached after the first run)"
    )
    next(r for r in rankers if r.name == "embedding").warm([n for ns in days.values() for n in ns])

    assessor, sample = None, set()
    if model_profiles:
        in_range = [o for o in world.outcomes if o.day and first <= o.day <= last]
        sample = bt.model_sample(in_range, model_profiles, model_days)
        budget = Budget(budget_usd)
        assessor = Assessor(provider, budget)
        fused = [r for r in rankers if r.name == "fused"]
        by_id = bt.profiles_by_id(world.panel.profiles)
        pairs = []
        for profile_id, day in sorted(sample):
            lists = process_day(
                day,
                days[day],
                [by_id[profile_id]],
                fused,
                versions=versions,
                mode="backtest",
                now=datetime.now(UTC),
                list_size=settings.list_size,
            )
            by_number = {n.publication_number: n for n in days[day]}
            pairs += [
                (by_id[profile_id], by_number[e.publication_number])
                for e in lists[0].entries[: settings.assess_top]
            ]
        typer.echo(f"model sample: {len(sample)} supplier-days, {len(pairs)} assessments")
        # Calls run a few at a time and are recorded as they return. The replay below
        # then reads the recordings, so the lists are built the same way every run.
        done = 0
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            for _result in pool.map(lambda pair: assessor(*pair), pairs):
                done += 1
                if done % 100 == 0 or done == len(pairs):
                    typer.echo(
                        f"  {done}/{len(pairs)} assessed, US${assessor.spent_usd:.3f} spent, "
                        f"{assessor.errors} errors, {assessor.over_budget} over budget"
                    )
        if assessor.last_error:
            typer.echo(f"  last model error: {assessor.last_error}")

    log = PredictionLog(log_path)
    written = bt.replay(
        world,
        rankers,
        versions,
        log,
        start=first,
        end=last,
        list_size=settings.list_size,
        assess=assessor,
        assess_top=settings.assess_top,
        assess_on=sample,
        progress=lambda line: None,
    )
    OutcomeLog(outcome_path).append(world.outcomes)
    award_days = store.days(AWARD, PROFILE_START, date.today())
    meta = {
        "divisions": list(settings.divisions),
        "profile_start": PROFILE_START.isoformat(),
        "cut": CUT_DATE.isoformat(),
        "test_end": TEST_END.isoformat(),
        "held_out_start": HELD_OUT_START.isoformat(),
        "awards_before_cut": store.count(AWARD, PROFILE_START, CUT_DATE - timedelta(days=1)),
        "awards_until": award_days[-1].isoformat() if award_days else None,
        "awards": world.awards_loaded,
        "suppliers": world.suppliers,
        "shared_identifiers": world.index.shared_identifiers,
        "eligible": world.panel.eligible,
        "panel": len(world.panel.profiles),
        "min_past_wins": MIN_PAST_WINS,
        "min_test_wins": MIN_TEST_WINS,
        "tenders": len(world.test_procedures),
        "days": len(world.notices_by_day()),
        "embedder": embedder.name,
        "versions": versions,
    }
    (settings.log_dir / report.BACKTEST_META).write_text(
        json.dumps(meta, indent=1) + "\n", encoding="utf-8"
    )
    # The live run uses the same suppliers with everything they have won to date.
    live_profiles = [
        build_profile(profile_id, world.wins[key].values())
        for profile_id, key in world.panel.keys.items()
    ]
    save_profiles(live_profiles, settings.profiles_dir / PANEL_FILE)
    (settings.profiles_dir / ALIASES_FILE).write_text(
        json.dumps(export_aliases(world.index, world.panel.keys), indent=0) + "\n", encoding="utf-8"
    )
    typer.echo(f"lists written: {written}; outcomes: {len(world.outcomes)}")
    if assessor:
        typer.echo(
            f"model: {assessor.calls} answers ({assessor.replayed} from recordings), "
            f"US${assessor.spent_usd:.4f} spent now, {assessor.no_recording} without a recording"
        )
    store.close()


@app.command("eval")
def evaluate(
    out: str = typer.Option(None, help="Where to write the report, default docs/eval-report.md"),
) -> None:
    """Score the committed logs and write the evaluation report. Needs no key and no feed."""
    settings = load_settings()
    target = Path(out) if out else settings.log_dir.parent.parent / "docs" / "eval-report.md"
    report.write(settings, target)
    data = report.collect(settings)
    typer.echo(f"report written to {target}")
    for name in ("development", "held_out", "model_sample_development", "model_sample_held_out"):
        typer.echo(f"\n{name.replace('_', ' ')}:")
        typer.echo(
            report.table(data[name])
            if any(s.outcomes for s in data[name])
            else "  nothing to score"
        )


def _read_health(settings: Settings) -> dict | None:
    path = settings.scoreboard_dir / HEALTH_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


@app.command()
def run(
    today: str = typer.Option(None, help="Run as if today were this day, YYYY-MM-DD"),
    lookback_days: int = typer.Option(14, help="How far back the feed is read"),
    model: bool = typer.Option(True, help="Let the model read the client's list if a key is set"),
) -> None:
    """Today's run: read the feed, write today's lists, check the feed's health."""
    settings = load_settings()
    now = datetime.now(UTC)
    current = _day(today, now.date())
    since = current - timedelta(days=lookback_days)
    store = Store(settings.db_path)
    client = TedClient(pause_s=settings.feed_pause_s, user_agent=settings.user_agent)
    feed_error, fetched = None, 0
    try:
        for kind in (COMPETITION, AWARD):
            fetched += fetch_range(
                client,
                store,
                kind,
                settings.divisions,
                since,
                current,
                today=current,
                force=True,
                progress=typer.echo,
            )
    except FeedError as exc:
        feed_error = str(exc)
        typer.echo(f"feed error: {exc}", err=True)
    finally:
        client.close()

    if feed_error is None:
        panel = load_profiles(settings.profiles_dir / PANEL_FILE)
        client_profile = load_client_profile(settings.client_profile)
        embedder = bt.make_embedder(settings)
        rankers = bt.build_rankers(None, embedder)
        log = PredictionLog(settings.log_dir / report.LIVE_LOG)
        assessor = None
        use_model = model and resolve_api_key() is not None
        provider = _provider(settings, "live.jsonl", live=use_model, record=use_model)
        if use_model:
            budget = Budget(settings.daily_cap_usd, spent_usd=log.cost_on(now.date().isoformat()))
            assessor = Assessor(provider, budget)
        versions = bt.ranker_versions(
            settings,
            embedder.name,
            model=provider.model,
            effort=str(provider.params.get("effort")),
            prompt=SYSTEM_PROMPT,
        )
        result = run_live(
            new_procedures=lambda day: store.new_procedures(day, day),
            available_days=store.days(COMPETITION, since, current),
            day_index=DayIndex(settings.log_dir / report.LIVE_DAYS),
            log=log,
            panel=panel,
            client=client_profile,
            rankers=rankers,
            versions=versions,
            list_size=settings.list_size,
            today=current,
            assess=assessor,
            assess_top=settings.assess_top,
            now=now,
        )
        typer.echo(
            f"days processed: {[d.isoformat() for d in result.days]}; tenders: {result.tenders}; "
            f"lists written: {result.lists_written}"
        )
        if assessor:
            typer.echo(
                f"model: {assessor.calls} assessments, US${assessor.spent_usd:.4f} spent, "
                f"{assessor.over_budget} skipped at the daily cap, {assessor.errors} errors"
            )
        elif model:
            typer.echo("model: no key found, the client's list was written without assessments")

    counts = {
        day: store.count(COMPETITION, day, day) for day in store.days(COMPETITION, since, current)
    }
    health = assess_health(
        now=now,
        today=current,
        feed_error=feed_error,
        fetched=fetched,
        parse_failures=store.parse_failures,
        latest_day=store.latest_day(COMPETITION),
        counts_by_day=counts,
    )
    health.write(settings.scoreboard_dir / HEALTH_FILE)
    for check in health.checks:
        typer.echo(f"  [{'ok' if check.ok else 'FAIL'}] {check.name}: {check.detail}")
    store.close()
    if not health.ok:
        raise typer.Exit(code=1)


@app.command()
def truth(
    since: str = typer.Option(None, "--from", help="Read awards published from this day"),
) -> None:
    """Record outcomes: awards in the store that name a panel supplier as winner."""
    settings = load_settings()
    day_index = DayIndex(settings.log_dir / report.LIVE_DAYS)
    days = day_index.days()
    if not days:
        typer.echo("the live run has not listed any tender yet")
        return
    store = Store(settings.db_path)
    awards = store.awards(_day(since, min(days)), date.today())
    aliases = json.loads((settings.profiles_dir / ALIASES_FILE).read_text(encoding="utf-8"))
    panel = load_profiles(settings.profiles_dir / PANEL_FILE)
    outcomes = live_outcomes(awards, aliases, day_index, panel)
    added = OutcomeLog(settings.log_dir / report.LIVE_OUTCOMES).append(outcomes)
    typer.echo(
        f"awards read: {len(awards)}; outcomes for listed tenders: {len(outcomes)}; new: {added}"
    )
    store.close()


@app.command("scoreboard")
def write_scoreboard() -> None:
    """Write scoreboard.json and the static page from the committed logs."""
    settings = load_settings()
    page = scoreboard.write(settings, health=_read_health(settings))
    typer.echo(f"scoreboard written to {page}")


@app.command()
def health() -> None:
    """Print the last run's feed checks. Exits 1 if any failed."""
    settings = load_settings()
    last = _read_health(settings)
    if last is None:
        typer.echo("no run has recorded the feed's health yet")
        raise typer.Exit(code=1)
    typer.echo(f"checked {last['checked_at']}")
    for check in last["checks"]:
        typer.echo(f"  [{'ok' if check['ok'] else 'FAIL'}] {check['name']}: {check['detail']}")
    if not last["ok"]:
        raise typer.Exit(code=1)


@app.command("today")
def show_today(
    day: str = typer.Option(None, help="Publication day to show, default the latest"),
) -> None:
    """Print the client's latest list."""
    settings = load_settings()
    lists = [
        s
        for s in PredictionLog(settings.log_dir / report.LIVE_LOG).read()
        if s.profile_id == "client"
    ]
    if not lists:
        typer.echo("no list has been written for the client yet")
        return
    wanted = _day(day, max(s.day for s in lists))
    chosen = [s for s in lists if s.day == wanted]
    best = next((s for s in chosen if s.ranker == "fused+model"), chosen[0] if chosen else None)
    if best is None:
        typer.echo(f"no list for {wanted}")
        return
    typer.echo(
        f"{best.day}: {len(best.entries)} tenders listed of {best.candidates} that passed "
        f"the rules; removed: {best.removed or 'none'}"
    )
    for entry in best.entries:
        verdict = (
            f"{entry.assessment.decision}, fit {entry.assessment.fit}"
            if entry.assessment
            else "not read"
        )
        typer.echo(f"{entry.rank:>3}. {entry.publication_number}  [{verdict}]")
        if entry.assessment:
            typer.echo(f"     {entry.assessment.reason}")


@app.command()
def demo(
    stub_embedder: bool = typer.Option(
        False, help="Rank without downloading the embedding model (word hashing instead)"
    ),
    live: bool = typer.Option(False, "--live", help="Allow paid calls for missing recordings"),
    record: bool = typer.Option(False, help="Save model responses to the fixture cassette"),
    assess_all: bool = typer.Option(False, help="Let the model read every tender that passed"),
) -> None:
    """The client's list for one real day of tenders. No feed, no key, no database."""
    settings = load_settings()
    notices, failures = demo_module.load_fixture(demo_module.fixture_path(settings.fixture_dir))
    client_profile = load_client_profile(settings.client_profile)
    embedder = HashEmbedder() if stub_embedder else bt.make_embedder(settings)
    rankers = bt.build_rankers(None, embedder)
    provider = _provider(settings, "fixture.jsonl", live=live, record=record)
    assessor = Assessor(provider, Budget(0.25) if live else None)
    shortlist = demo_module.client_list(
        notices,
        client_profile,
        rankers,
        assess=assessor,
        list_size=len(notices) if assess_all else settings.list_size,
        assess_top=len(notices) if assess_all else settings.assess_top,
    )
    typer.echo(demo_module.format_list(shortlist, notices))
    typer.echo(
        f"\nrecords that could not be read: {failures}; model answers: {assessor.calls} "
        f"({assessor.replayed} from the recording, US${assessor.spent_usd:.4f} spent now); "
        f"without a recording: {assessor.no_recording}"
    )


if __name__ == "__main__":
    app()
