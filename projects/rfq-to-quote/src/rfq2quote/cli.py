"""Command line: run RFQs through the agent, evaluate it, watch the queue."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from . import outside
from .evals import CONFIGS, results_json, run_eval
from .intake import SUPPORTED, load_document
from .llm import DEFAULT_EFFORT, DEFAULT_MODEL, CassetteMiss
from .models import PipelineResult
from .pipeline import DATA_DIR, PROJECT_ROOT, build_pipeline, load_catalog
from .store import ReviewStore

app = typer.Typer(add_completion=False, no_args_is_help=True, help=__doc__)

Mode = Annotated[str, typer.Option(help="'llm' (Claude) or 'baseline' (rules only, no API).")]
Model = Annotated[str, typer.Option(envvar="RFQ2QUOTE_MODEL", help="Claude model id.")]
Effort = Annotated[str, typer.Option(envvar="RFQ2QUOTE_EFFORT", help="Model reasoning effort.")]
Live = Annotated[bool, typer.Option("--live", help="Call the API when nothing is recorded.")]
Record = Annotated[bool, typer.Option("--record", help="Call the API and save the responses.")]
Concurrency = Annotated[int, typer.Option(help="Model calls in flight at once.")]


def _summary(result: PipelineResult) -> str:
    lines = [
        f"{result.rfq_id}: {result.route.upper()}  "
        f"customer={result.rfq.customer_name!r}  delivery={result.rfq.delivery_date}  "
        f"total=EUR {result.quote.total:,.2f}"
    ]
    for match in result.matches:
        qty = "?" if match.item.quantity is None else f"{match.item.quantity:g}"
        lines.append(
            f"    {qty:>6} {match.item.unit or '':<3} {match.item.description[:44]:<44} "
            f"-> {match.sku or 'NO MATCH'} ({match.confidence})"
        )
    for flag in result.flags:
        lines.append(f"    [{flag.severity}] {flag.code}: {flag.detail}")
    if result.usage.llm_calls:
        lines.append(
            f"    llm: {result.usage.llm_calls} calls, ${result.usage.cost_usd:.4f}, "
            f"{result.usage.latency_s:.1f}s"
        )
    return "\n".join(lines)


@app.command()
def run(
    paths: Annotated[list[Path], typer.Argument(help="RFQ files or folders.")],
    mode: Mode = "llm",
    prompt: Annotated[str, typer.Option(help="Extraction prompt version.")] = "v2",
    model: Model = DEFAULT_MODEL,
    effort: Effort = DEFAULT_EFFORT,
    live: Live = False,
    record: Record = False,
    assisted: Annotated[
        bool, typer.Option("--assisted", help="Auto-approve nothing: every quote is reviewed.")
    ] = False,
    save: Annotated[bool, typer.Option(help="Store results in the review queue.")] = True,
    concurrency: Concurrency = 8,
    as_of: Annotated[
        str,
        typer.Option(
            help="Processing date for lead-time checks: 'today', 'received' (each document's "
            "own date, for replaying old mail) or YYYY-MM-DD."
        ),
    ] = "today",
) -> None:
    """Turn RFQ documents into draft quotes and route them."""
    files = []
    for path in paths:
        found = sorted(path.iterdir()) if path.is_dir() else [path]
        files += [p for p in found if p.suffix.lower() in SUPPORTED]
    if not files:
        raise typer.BadParameter("No supported RFQ files found.")
    fixed = None if as_of in ("today", "received") else date.fromisoformat(as_of)
    pipeline = build_pipeline(
        mode,
        prompt_version=prompt,
        model=model,
        effort=effort,
        live=live,
        record=record,
        assisted=assisted,
        concurrency=concurrency,
    )
    store = ReviewStore() if save else None
    routes: dict[str, int] = {}
    for path in files:
        when = fixed
        if as_of == "received":
            try:
                received = load_document(path).received_date
                when = date.fromisoformat(received) if received else None
            except Exception:  # noqa: BLE001 - run_file reports the unreadable file
                when = None
        try:
            result = pipeline.run_file(path, when)
        except CassetteMiss as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(2) from exc
        routes[result.route] = routes.get(result.route, 0) + 1
        typer.echo(_summary(result))
        if store:
            status = store.save(result)
            if status in ("approved", "corrected", "rejected"):
                typer.echo(f"    already {status} by a reviewer; decision kept")
    typer.echo(
        f"\n{len(files)} RFQs: {routes.get('auto_approve', 0)} auto-approved, "
        f"{routes.get('human_review', 0)} sent to human review."
    )
    if store:
        typer.echo("Review queue: uv run streamlit run app/review_ui.py")


@app.command(name="eval")
def eval_(
    configs: Annotated[str, typer.Option(help="Comma-separated configurations.")] = ",".join(
        CONFIGS
    ),
    out: Annotated[Path, typer.Option(help="Where to write the Markdown report.")] = (
        PROJECT_ROOT / "docs" / "eval-report.md"
    ),
    model: Model = DEFAULT_MODEL,
    effort: Effort = DEFAULT_EFFORT,
    live: Live = False,
    record: Record = False,
    concurrency: Concurrency = 8,
) -> None:
    """Score configurations against the labelled RFQs and write a report."""
    names = [c.strip() for c in configs.split(",") if c.strip()]
    unknown = [n for n in names if n not in CONFIGS]
    if unknown:
        raise typer.BadParameter(f"Unknown configuration(s) {unknown}; choose from {list(CONFIGS)}")
    report, reports = run_eval(
        names,
        DATA_DIR,
        model=model,
        effort=effort,
        live=live,
        record=record,
        concurrency=concurrency,
        log=lambda message: typer.echo(message, err=True),
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    typer.echo(results_json(reports))
    typer.echo(f"\nReport written to {out}")


@app.command(name="fetch-outside")
def fetch_outside() -> None:
    """Download the pinned third-party test file used by `eval-outside`."""
    try:
        path = outside.fetch()
    except (OSError, ValueError) as exc:
        typer.secho(f"Download failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    typer.echo(f"Saved {path} (checksum verified)")


@app.command(name="eval-outside")
def eval_outside(
    data: Annotated[Path, typer.Option(help="The downloaded pair file.")] = outside.DATA_PATH,
    limit: Annotated[int, typer.Option(help="Score only the first N queries (0 = all).")] = 0,
    out: Annotated[Path, typer.Option(help="Where to write the Markdown report.")] = (
        PROJECT_ROOT / "docs" / "eval-outside-data.md"
    ),
    cassette_dir: Annotated[
        Path, typer.Option(help="Where recorded responses are read and saved.")
    ] = outside.CASSETTE_DIR,
    model: Model = DEFAULT_MODEL,
    effort: Effort = DEFAULT_EFFORT,
    live: Live = False,
    record: Record = False,
    concurrency: Concurrency = 8,
    max_usd: Annotated[float, typer.Option(help="Stop live calls after this spend.")] = 1.0,
) -> None:
    """Score the matching step on a public benchmark the project's author did not write."""
    if not data.exists():
        typer.secho(
            f"{data} is not there. The data is not part of this repository; "
            "download it with: uv run rfq2quote fetch-outside",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    queries = outside.load_queries(data)
    total = len(queries)
    if limit:
        queries = queries[:limit]

    def log(message: str) -> None:
        typer.echo(message, err=True)

    if live or record:
        log(
            f"{len(queries)} queries, at most {outside.calls_needed(queries)} model calls "
            f"(fewer if some are recorded already). Live spend is capped at ${max_usd:.2f}."
        )
    try:
        results = outside.run_outside(
            queries,
            model=model,
            effort=effort,
            live=live,
            record=record,
            concurrency=concurrency,
            max_usd=max_usd,
            cassette_dir=cassette_dir,
            log=log,
        )
    except outside.SpendCapReached as exc:
        typer.secho(f"{exc} No report was written.", fg=typer.colors.RED, err=True)
        raise typer.Exit(3) from exc
    # Free: every model with a recording is replayed on the same queries.
    comparison = outside.compare_models(
        queries,
        against=model,
        effort=effort,
        cassette_dir=cassette_dir,
        concurrency=concurrency,
        log=log,
    )
    report = outside.render_report(
        results, queries, total_queries=total, model=model, effort=effort, comparison=comparison
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    typer.echo(json.dumps(results, indent=2))
    if len(comparison["models"]) > 1:
        typer.echo(json.dumps({"recorded_models": comparison}, indent=2))
    typer.echo(f"\nReport written to {out}")


@app.command()
def repeats() -> None:
    """Replay the recorded repeat runs through today's rules and print one row per run."""
    from .repeats import score_all, table

    typer.echo(table(score_all()))


@app.command()
def hard() -> None:
    """Replay every recorded run on the harder set and print one row per run."""
    from .hard import score_hard, table

    typer.echo(table(score_hard()))


@app.command()
def stats() -> None:
    """Review-queue numbers to watch after go-live: holds, overrides, cost."""
    typer.echo(json.dumps(ReviewStore().stats(), indent=2))


@app.command()
def search(
    text: Annotated[str, typer.Argument(help="Product text as a customer would write it.")],
    limit: int = 5,
) -> None:
    """Show which catalog SKUs the search step retrieves for a line of text."""
    for sku, score in load_catalog().search(text, limit):
        typer.echo(f"{score:6.2f}  {sku.sku:<22} {sku.description}")


if __name__ == "__main__":
    app()
