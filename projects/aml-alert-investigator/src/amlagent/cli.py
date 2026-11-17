"""Command line for the AML alert investigation agent.

Nothing here spends money unless `--live` is given.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from .bankdb import DATA_DIR, BankDB, load_alerts, load_gold, load_planted
from .baseline import investigate_baseline
from .budget import BudgetedProvider, Ledger
from .evals import Section, leak_scan, render_report, score_case, scrubber_catch_rate
from .llm import (
    AnthropicProvider,
    CassetteMiss,
    CassetteProvider,
    GuardedProvider,
    LLMProvider,
    RouterProvider,
    StepRequest,
    is_metered,
)
from .nim import NvidiaProvider
from .pipeline import RunReport, run_alerts
from .privacy import OutboundScanner
from .roles import CONFIGS, NIM_MODEL
from .store import CaseStore

app = typer.Typer(add_completion=False, help="AML alert investigation agent.")
PROJECT = Path(__file__).resolve().parents[2]
REPORT = PROJECT / "docs" / "eval-report.md"
LABELS = {
    "baseline": "Rules only",
    "haiku": "Haiku 5.5 in every role",
    "routed": "Routed: Haiku triage and workers, Opus lead",
    "opus": "Opus 5.5 on every alert, low effort, no triage tier",
    "nim": f"NVIDIA-hosted model in every role ({NIM_MODEL})",
}
NIM_CANDIDATES = [
    NIM_MODEL,
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "meta/llama-3.3-70b-instruct",
    "mistralai/mistral-nemotron",
]


def cassette_dir() -> Path:
    return Path(os.environ.get("AML_AGENT_CASSETTES") or DATA_DIR / "cassettes")


def ledger(max_usd: float | None = None) -> Ledger:
    cap = float(os.environ.get("AML_AGENT_BUDGET_USD", "5.00"))
    return Ledger(PROJECT / "var" / "spend.jsonl", cap_usd=cap, run_cap_usd=max_usd)


def make_provider(
    config: str, db: BankDB, *, live: bool, record: bool, max_usd: float | None
) -> LLMProvider:
    inner = None
    if live:
        router = RouterProvider(anthropic=AnthropicProvider(), nvidia=NvidiaProvider())
        inner = BudgetedProvider(router, ledger(max_usd))
    cassette = CassetteProvider(cassette_dir() / f"{config}.json", inner=inner, record=record)
    return GuardedProvider(cassette, OutboundScanner(db.identifier_inventory()))


def _select(alert: str | None, split: str) -> list:
    alerts = load_alerts()
    if alert:
        alerts = [a for a in alerts if a.alert_id == alert]
        if not alerts:
            raise typer.BadParameter(f"No alert {alert}.")
    return [a for a in alerts if split == "all" or a.split == split]


def _run(
    config: str,
    alerts: list,
    db: BankDB,
    *,
    live: bool,
    record: bool,
    max_usd: float | None,
    mode: str,
) -> RunReport:
    if config == "baseline":
        return RunReport(cases=[investigate_baseline(a, db=db, mode=mode) for a in alerts])
    provider = make_provider(config, db, live=live, record=record, max_usd=max_usd)
    return run_alerts(alerts, db=db, provider=provider, cfg=CONFIGS[config], mode=mode)


@app.command()
def run(
    config: str = typer.Option("baseline", help="baseline, haiku, routed, opus or nim"),
    alert: str = typer.Option(None, help="One alert id, for example AL-001"),
    split: str = typer.Option("all", help="dev, heldout or all"),
    live: bool = typer.Option(False, help="Call the model API for anything not recorded"),
    record: bool = typer.Option(False, help="Save live responses for replay"),
    max_usd: float = typer.Option(0.50, help="Spend cap for this run"),
    mode: str = typer.Option("assisted", help="assisted or auto-close"),
) -> None:
    """Investigate alerts and save the cases."""
    if config != "baseline" and config not in CONFIGS:
        raise typer.BadParameter(f"Unknown configuration {config}.")
    db, store = BankDB(), CaseStore()
    try:
        report = _run(
            config, _select(alert, split), db, live=live, record=record, max_usd=max_usd, mode=mode
        )
    except CassetteMiss as exc:
        typer.echo(str(exc))
        raise typer.Exit(2) from exc
    for result, vault in report.cases:
        store.save(result, vault)
        outcome = result.disposition.outcome if result.disposition else "-"
        rules = ",".join(r.code for r in result.rules) or "-"
        typer.echo(
            f"{result.alert_id}  {config:<8} {result.tier:<8} {result.queue:<21} "
            f"{outcome:<21} ${result.cost_usd:.4f}  {rules}"
        )
    total = sum(result.cost_usd for result, _ in report.cases)
    typer.echo(f"{len(report.cases)} cases, ${total:.4f}")
    if report.stopped:
        typer.echo(f"Stopped early: {report.stopped}")


@app.command()
def trace(
    alert: str,
    config: str = typer.Option("baseline"),
    reveal: bool = typer.Option(False, help="Restore real values (analyst view)"),
) -> None:
    """Print the audit trail of one case."""
    found = CaseStore().get(alert, config)
    if found is None:
        typer.echo(
            f"No case for {alert} under {config}. "
            f"Run: aml-agent run --config {config} --alert {alert}"
        )
        raise typer.Exit(1)
    result, vault = found
    show = vault.reidentify if reveal else (lambda value: value)
    typer.echo(
        f"{result.alert_id}  tier={result.tier}  queue={result.queue}  cost=${result.cost_usd:.4f}"
    )
    typer.echo(f"hard flags: {', '.join(result.hard_flags) or 'none'}")
    for rule in result.rules:
        typer.echo(f"rule {rule.code}: {rule.message}")
    for number, step in enumerate(result.steps, 1):
        typer.echo(
            f"{number}. {step.role} [{step.model}] in={step.input_tokens} out={step.output_tokens} "
            f"${step.cost_usd:.4f}"
        )
        for call in step.tool_calls:
            mark = "error" if call.is_error else "ok"
            typer.echo(
                f"   {call.name}({show(json.dumps(call.input))}) -> {mark}: "
                f"{show(call.output)[:300]}"
            )
    for entry in result.sensitive_requests:
        typer.echo(
            f"sensitive request ({'granted' if entry['granted'] else 'denied'}): "
            f"{show(entry['counterparty'])}: {entry['justification']}"
        )
    d = result.disposition
    if d is None:
        typer.echo("No recommendation: investigate manually.")
        return
    typer.echo(
        f"recommendation: {d.outcome} ({d.reason_code or d.typology or '-'}), "
        f"confidence {d.confidence}"
    )
    for finding in d.findings:
        typer.echo(
            f" - {show(finding.statement)} [{', '.join(show(e) for e in finding.evidence_ids)}]"
        )
    if d.narrative:
        typer.echo(show(d.narrative))


@app.command("eval")
def evaluate(
    out: Annotated[Path, typer.Option()] = REPORT,
    live: bool = typer.Option(False),
    record: bool = typer.Option(False),
    max_usd: float = typer.Option(0.50),
) -> None:
    """Score every configuration and write the report."""
    db, golds, alerts = BankDB(), load_gold(), load_alerts()
    splits = {a.alert_id: a.split for a in alerts}
    sections = []
    for name in ("baseline", *CONFIGS):
        metered = name == "baseline" or is_metered(CONFIGS[name].lead_model)
        try:
            report = _run(
                name, alerts, db, live=live, record=record, max_usd=max_usd, mode="assisted"
            )
        except CassetteMiss:
            sections.append(Section(name, LABELS[name], None, metered, "not recorded"))
            continue
        rows = [
            score_case(result, vault, golds[result.alert_id], splits[result.alert_id])
            for result, vault in report.cases
        ]
        note = f" (stopped early: {report.stopped})" if report.stopped else ""
        sections.append(Section(name, LABELS[name] + note, rows, metered))
    identifiers = db.identifier_inventory() | set(load_planted())
    leaks = {p.name: leak_scan(p, identifiers) for p in sorted(cassette_dir().glob("*.json"))}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_report(sections, scrubber=scrubber_catch_rate(db, load_planted()), leaks=leaks),
        encoding="utf-8",
    )
    typer.echo(f"Wrote {out}")
    if any(leaks.values()):
        raise typer.Exit(1)


@app.command("leak-check")
def leak_check() -> None:
    """Scan every recording for real identifiers. Exits 1 if any is found."""
    db = BankDB()
    identifiers = db.identifier_inventory() | set(load_planted())
    found = False
    for path in sorted(cassette_dir().glob("*.json")):
        hits = leak_scan(path, identifiers)
        typer.echo(
            f"{path.name}: {'clean' if not hits else f'{len(hits)} requests with identifiers'}"
        )
        for tag, kinds in hits:
            typer.echo(f"  {tag}: {', '.join(kinds)}")
        found = found or bool(hits)
    if found:
        raise typer.Exit(1)
    typer.echo("No real identifier in any recording.")


@app.command()
def budget() -> None:
    """Show what live runs have cost so far."""
    book = ledger()
    typer.echo(f"Spent ${book.spent():.4f} of ${book.cap_usd:.2f}")


@app.command("nim-check")
def nim_check(
    models: str = typer.Option(",".join(NIM_CANDIDATES), help="Comma-separated model ids"),
    yes: bool = typer.Option(False, "--yes", help="Confirm: this makes live calls"),
) -> None:
    """Ask NVIDIA-hosted models for one tool call each and report which ones comply."""
    if not yes:
        typer.echo(
            "This makes one live request per model on the NVIDIA trial tier. Re-run with --yes."
        )
        raise typer.Exit(1)
    probe = {
        "name": "get_prior_alerts",
        "description": "Earlier alerts on a customer.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"customer": {"type": "string"}},
            "required": ["customer"],
            "additionalProperties": False,
        },
    }
    provider = NvidiaProvider(max_requests=20)
    for model in [m.strip() for m in models.split(",") if m.strip()]:
        request = StepRequest(
            model=model,
            system="You are a reviewer. Use the tool to answer.",
            messages=[{"role": "user", "content": "Look up earlier alerts for customer CUST_01."}],
            tools=[probe],
            max_tokens=500,
            tag="nim-check",
        )
        try:
            response = provider.step(request)
        except Exception as exc:  # report every model, whatever went wrong with one
            typer.echo(f"{model}: failed ({exc})")
            continue
        calls = [b for b in response.content if b.get("type") == "tool_use"]
        ok = bool(calls) and calls[0]["input"] == {"customer": "CUST_01"}
        typer.echo(
            f"{model}: {'tool call ok' if ok else 'no usable tool call'} "
            f"(stop={response.stop_reason}, {response.latency_s}s)"
        )
