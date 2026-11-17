"""Evaluation: score a pipeline configuration against hand-labelled RFQs.

What is measured, and why:

- header field accuracy   did we read who, when and where correctly
- item extraction F1      did we find the right (quantity, unit) pairs
- SKU match accuracy      matcher alone, fed the gold line text (isolates matching)
- end-to-end line F1      (sku, quantity, unit) triples: what actually lands on a quote
- routing                 of quotes a person must see, how many reached one; how many
                          clean quotes were held anyway; and how many wrong quotes
                          were auto-approved (the costly failure)
- cost and latency        per RFQ

The expected route is not hand-labelled: it is what the guardrails decide when
given the gold extraction and gold SKUs, so it always matches current policy.

Cases carry a split. `dev` cases were visible while the prompts were written.
`holdout` cases were written after the prompts were frozen and are reported
separately. `hard` cases were added later: their labels were chosen in code and
their texts written by a model around them.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from collections.abc import Collection
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from pydantic import BaseModel

from .intake import load_document
from .llm import CassetteMiss
from .match import Matcher
from .models import RFQ, LineItem, MatchedLine, PipelineResult, Usage
from .pipeline import DATA_DIR, Pipeline, build_pipeline, load_catalog

CONFIGS = {
    "baseline": dict(mode="baseline"),
    "llm-v1": dict(mode="llm", prompt_version="v1"),
    "llm-v2": dict(mode="llm", prompt_version="v2"),
}
HEADER_FIELDS = ("customer_name", "customer_email", "delivery_date", "delivery_location")
SPLITS = {"dev": "Development set", "holdout": "Held-out set", "hard": "Harder set"}
# The 40 requests written by hand. Published figures and the repeat runs refer to these.
ORIGINAL_SPLITS = ("dev", "holdout")
# The matcher on its own is scored per group, so the figure for the original requests
# does not move when a set is added.
MATCHER_GROUPS = {"Development and held-out sets": ORIGINAL_SPLITS, "Harder set": ("hard",)}


class GoldItem(BaseModel):
    text: str
    sku: str | None
    quantity: float | None
    unit: str | None


class GoldCase(BaseModel):
    id: str
    source: str
    received: str
    hard: str = ""
    split: str = "dev"
    customer_name: str | None
    customer_email: str | None
    delivery_date: str | None
    delivery_location: list[str]
    # True when a careful sales rep would have something to ask or handle by hand.
    open_questions: bool = False
    items: list[GoldItem]


def load_gold(data_dir: Path = DATA_DIR, splits: Collection[str] | None = None) -> list[GoldCase]:
    """Labels, plus reviewer corrections for requests that have no label of their own.

    Ordered set by set as in `SPLITS`, then by id. `splits` keeps only those sets.
    """
    cases: dict[str, GoldCase] = {}
    for folder in ("gold", "corrections"):
        for path in sorted((data_dir / folder).glob("*.json")):
            gold_case = GoldCase.model_validate_json(path.read_text(encoding="utf-8"))
            cases.setdefault(gold_case.id, gold_case)  # a hand label wins over a correction
    order = {split: n for n, split in enumerate(SPLITS)}
    kept = [c for c in cases.values() if splits is None or c.split in splits]
    return sorted(kept, key=lambda c: (order.get(c.split, len(order)), c.id))


# --- scoring primitives -------------------------------------------------------


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9äöüß]", "", (text or "").lower())


def score_fields(rfq: RFQ, gold: GoldCase) -> dict[str, bool]:
    name, gold_name = _norm(rfq.customer_name), _norm(gold.customer_name)
    location = _norm(rfq.delivery_location)
    return {
        "customer_name": (name == gold_name)
        or bool(name and gold_name and (name in gold_name or gold_name in name)),
        "customer_email": (rfq.customer_email or "").lower() == (gold.customer_email or "").lower(),
        "delivery_date": rfq.delivery_date == gold.delivery_date,
        "delivery_location": (
            any(_norm(alt) in location for alt in gold.delivery_location)
            if gold.delivery_location
            else not location
        ),
    }


def _qty_unit(quantity: float | None, unit: str | None) -> tuple:
    return (None, None) if quantity is None else (round(float(quantity), 3), unit)


def overlap(predicted: list, gold: list) -> int:
    """Size of the multiset intersection."""
    return sum((Counter(predicted) & Counter(gold)).values())


def f1(tp: int, n_pred: int, n_gold: int) -> float:
    if n_pred == 0 and n_gold == 0:
        return 1.0
    if tp == 0:
        return 0.0
    precision, recall = tp / n_pred, tp / n_gold
    return 2 * precision * recall / (precision + recall)


# --- per-case and per-config scoring -----------------------------------------


@dataclass
class CaseScore:
    id: str
    hard: str
    split: str
    fields: dict[str, bool]
    item_tp: int
    item_pred: int
    line_tp: int
    line_pred: int
    n_gold: int
    expected_route: str
    route: str
    flags: list[str]
    usage: Usage

    @property
    def lines_exact(self) -> bool:
        return self.line_tp == self.line_pred == self.n_gold

    @property
    def failed(self) -> bool:
        return "PIPELINE_ERROR" in self.flags

    @property
    def unsafe_auto_approval(self) -> bool:
        """Approved although a line, the recipient or the date is wrong, or policy says hold."""
        return self.route == "auto_approve" and not (
            self.lines_exact
            and self.fields["customer_email"]
            and self.fields["delivery_date"]
            and self.expected_route == "auto_approve"
        )


def summarise(cases: list[CaseScore]) -> dict[str, float]:
    n = len(cases)
    must_review = [c for c in cases if c.expected_route == "human_review"]
    may_auto = [c for c in cases if c.expected_route == "auto_approve"]
    n_gold = sum(c.n_gold for c in cases)
    out = {f"field:{name}": sum(c.fields[name] for c in cases) / n for name in HEADER_FIELDS}
    out |= {
        "cases": float(n),
        "field_accuracy": sum(sum(c.fields.values()) for c in cases) / (n * len(HEADER_FIELDS)),
        "item_f1": f1(sum(c.item_tp for c in cases), sum(c.item_pred for c in cases), n_gold),
        "line_f1": f1(sum(c.line_tp for c in cases), sum(c.line_pred for c in cases), n_gold),
        "exact_quote_rate": sum(c.lines_exact for c in cases) / n,
        "must_review": float(len(must_review)),
        "review_recall": (
            sum(c.route == "human_review" for c in must_review) / len(must_review)
            if must_review
            else 1.0
        ),
        # Clean requests held anyway: costs reviewer time, never money.
        "unnecessary_holds": (
            sum(c.route == "human_review" for c in may_auto) / len(may_auto) if may_auto else 0.0
        ),
        "auto_approve_rate": sum(c.route == "auto_approve" for c in cases) / n,
        "auto_approve_ceiling": len(may_auto) / n,
        "unsafe_auto_approvals": float(sum(c.unsafe_auto_approval for c in cases)),
        "pipeline_errors": float(sum(c.failed for c in cases)),
        "cost_per_rfq_usd": sum(c.usage.cost_usd for c in cases) / n,
        "latency_per_rfq_s": sum(c.usage.latency_s for c in cases) / n,
        "llm_calls_per_rfq": sum(c.usage.llm_calls for c in cases) / n,
    }
    return out


@dataclass
class ConfigReport:
    name: str
    cases: list[CaseScore] = field(default_factory=list)
    skipped: str | None = None

    def split(self, split: str) -> list[CaseScore]:
        return [c for c in self.cases if c.split == split]

    def metrics(self, split: str | None = None) -> dict[str, float]:
        return summarise(self.split(split) if split else self.cases)


def oracle_result(pipeline: Pipeline, gold: GoldCase, data_dir: Path = DATA_DIR) -> PipelineResult:
    """What the pipeline decides when extraction and matching are perfect."""
    rfq = RFQ(
        customer_name=gold.customer_name,
        customer_email=gold.customer_email,
        delivery_date=gold.delivery_date,
        delivery_location=gold.delivery_location[0] if gold.delivery_location else None,
        line_items=[
            LineItem(description=i.text, quantity=i.quantity, unit=i.unit) for i in gold.items
        ],
        missing_info=["Open point for the sales rep (gold label)."] if gold.open_questions else [],
    )
    matches = [
        MatchedLine(
            item=item,
            sku=g.sku,
            confidence="high" if g.sku else "low",
            reason="gold label" if g.sku else "Not in catalog or not identifiable (gold label).",
        )
        for item, g in zip(rfq.line_items, gold.items, strict=True)
    ]
    facts = load_document(data_dir / gold.source).facts()
    as_of = date.fromisoformat(gold.received)
    return pipeline.finish(gold.id, gold.source, rfq, matches, as_of, Usage(), facts)


def score_case(pipeline: Pipeline, gold: GoldCase, data_dir: Path = DATA_DIR) -> CaseScore:
    doc = load_document(data_dir / gold.source)
    result = pipeline.run(doc, as_of=date.fromisoformat(gold.received))
    gold_items = [_qty_unit(i.quantity, i.unit) for i in gold.items]
    gold_lines = [(i.sku, *_qty_unit(i.quantity, i.unit)) for i in gold.items]
    pred_items = [_qty_unit(m.item.quantity, m.item.unit) for m in result.matches]
    pred_lines = [(m.sku, *_qty_unit(m.item.quantity, m.item.unit)) for m in result.matches]
    return CaseScore(
        id=gold.id,
        hard=gold.hard,
        split=gold.split,
        fields=score_fields(result.rfq, gold),
        item_tp=overlap(pred_items, gold_items),
        item_pred=len(pred_items),
        line_tp=overlap(pred_lines, gold_lines),
        line_pred=len(pred_lines),
        n_gold=len(gold.items),
        expected_route=oracle_result(pipeline, gold, data_dir).route,
        route=result.route,
        flags=sorted({f.code for f in result.flags if f.severity == "review"}),
        usage=result.usage,
    )


class Progress:
    """Report progress as work finishes: one line per `every` items and one at the end."""

    def __init__(self, label: str, total: int, every: int = 8, log=None):
        self.label, self.total, self.every, self.log = label, total, max(1, every), log
        self.done, self.started, self._lock = 0, time.perf_counter(), threading.Lock()

    def wrap(self, fn):
        def run(item):
            try:
                return fn(item)
            finally:
                self.tick()

        return run

    def tick(self) -> None:
        with self._lock:
            self.done += 1
            if self.log and (self.done % self.every == 0 or self.done == self.total):
                elapsed = time.perf_counter() - self.started
                self.log(f"[{self.label}] {self.done}/{self.total} done, {elapsed:.0f}s")


def evaluate_config(
    name: str,
    gold: list[GoldCase],
    data_dir: Path = DATA_DIR,
    workers: int = 1,
    log=None,
    **kw,
):
    report = ConfigReport(name=name)
    progress = Progress(f"{name}: requests", len(gold), workers, log)
    try:
        pipeline = build_pipeline(**CONFIGS[name], data_dir=data_dir, **kw)
        score = progress.wrap(lambda g: score_case(pipeline, g, data_dir))
        with ThreadPoolExecutor(max(1, workers)) as pool:
            report.cases = list(pool.map(score, gold))
    except CassetteMiss as exc:
        report.cases, report.skipped = [], str(exc)
    return report


def score_matcher(
    matcher: Matcher, gold: list[GoldCase], workers: int = 1, log=None
) -> dict[str, float]:
    """Matcher in isolation: gold line text in, SKU out."""
    labels = [g for gold_case in gold for g in gold_case.items]
    progress = Progress(f"{matcher.name} in isolation: lines", len(labels), workers, log)

    def match_one(g: GoldItem):
        line_usage = Usage()
        item = LineItem(description=g.text, quantity=g.quantity, unit=g.unit)
        return matcher.match(item, line_usage), line_usage

    with ThreadPoolExecutor(max(1, workers)) as pool:
        results = list(pool.map(progress.wrap(match_one), labels))
    total, correct, wrong_confident, usage = len(labels), 0, 0, Usage()
    for g, (match, line_usage) in zip(labels, results, strict=True):
        usage.add(line_usage)
        correct += match.sku == g.sku
        wrong_confident += (
            match.sku != g.sku and match.sku is not None and match.confidence == "high"
        )
    return {
        "lines": float(total),
        "sku_accuracy": correct / total,
        "wrong_sku_high_confidence": float(wrong_confident),
        "cost_usd": usage.cost_usd,
    }


# --- report ------------------------------------------------------------------

_ROWS = [
    ("field_accuracy", "Header field accuracy", "pct"),
    ("item_f1", "Line item extraction F1 (quantity, unit)", "pct"),
    ("line_f1", "End-to-end line F1 (SKU, quantity, unit)", "pct"),
    ("exact_quote_rate", "RFQs with every line exactly right", "pct"),
    ("review_recall", "Must-review quotes that reached a person", "pct"),
    ("unnecessary_holds", "Clean quotes held anyway", "pct"),
    ("auto_approve_rate", "Auto-approved share", "pct"),
    ("unsafe_auto_approvals", "Unsafe auto-approvals (count)", "int"),
    ("pipeline_errors", "Requests that failed and went to a person (count)", "int"),
    ("cost_per_rfq_usd", "LLM cost per RFQ (USD)", "usd"),
    ("latency_per_rfq_s", "LLM time per RFQ, summed over calls (s)", "sec"),
    ("llm_calls_per_rfq", "LLM calls per RFQ", "num"),
]


def _fmt(value: float, kind: str) -> str:
    return {
        "pct": f"{value * 100:.1f}%",
        "int": f"{value:.0f}",
        "usd": f"${value:.4f}",
        "sec": f"{value:.1f}",
        "num": f"{value:.1f}",
    }[kind]


def _split_table(ran: list[ConfigReport], split: str, title: str) -> list[str]:
    all_metrics = [r.metrics(split) for r in ran if r.split(split)]
    names = [r.name for r in ran if r.split(split)]
    if not all_metrics:
        return []
    first = all_metrics[0]
    lines = [
        f"## {title}",
        "",
        f"{first['cases']:.0f} RFQs. Policy requires a person on {first['must_review']:.0f} of "
        f"them, so at most {first['auto_approve_ceiling'] * 100:.1f}% can be auto-approved.",
        "",
        "| Metric | " + " | ".join(f"`{n}`" for n in names) + " |",
        "|---|" + "---:|" * len(names),
    ]
    for key, label, kind in _ROWS:
        lines.append(f"| {label} | " + " | ".join(_fmt(m[key], kind) for m in all_metrics) + " |")
    lines += [
        "",
        "Header accuracy by field:",
        "",
        "| Field | " + " | ".join(f"`{n}`" for n in names) + " |",
        "|---|" + "---:|" * len(names),
    ]
    for name in HEADER_FIELDS:
        cells = " | ".join(_fmt(m[f"field:{name}"], "pct") for m in all_metrics)
        lines.append(f"| {name} | {cells} |")
    return lines + [""]


def render_report(
    reports: list[ConfigReport],
    matcher_scores: dict[str, dict[str, dict[str, float] | str]],
    gold: list[GoldCase],
    model: str,
    effort: str,
) -> str:
    ran = [r for r in reports if not r.skipped]
    lines = [
        "# Evaluation report",
        "",
        f"Generated {date.today().isoformat()} by `rfq2quote eval` on {len(gold)} labelled RFQs "
        f"({sum(len(g.items) for g in gold)} line items). LLM configurations use `{model}` at "
        f"`{effort}` effort.",
        "",
        "| Configuration | What it is |",
        "|---|---|",
        "| `baseline` | Regular expressions for extraction (delivery location is not "
        "attempted), top search hit for matching. No LLM. |",
        "| `llm-v1` | Claude extraction with a three-sentence prompt, Claude matching. |",
        "| `llm-v2` | Claude extraction with the full rule prompt, Claude matching. |",
        "",
        "The development set was visible while the prompts were written. The held-out set "
        "was written after the prompts were frozen. The harder set was added later: its "
        "labels were chosen in code and its texts were written by a model, Claude Opus 5.5, "
        "around them. All three are small, so read counts, not decimals.",
        "",
    ]
    for r in reports:
        if r.skipped:
            lines += [f"> `{r.name}` was **not run**: {r.skipped}", ""]
    for split, title in SPLITS.items():
        lines += _split_table(ran, split, title)

    lines += [
        "## Matcher in isolation",
        "",
        "Each matcher is given the gold line text, so extraction errors are excluded. The "
        "gold text is a clean, self-contained description, which is easier than raw wording.",
        "",
        "| Matcher | Sets | SKU accuracy | Wrong SKU with high confidence | Cost (USD) |",
        "|---|---|---:|---:|---:|",
    ]
    for name, groups in matcher_scores.items():
        for title, score in groups.items():
            if isinstance(score, str):
                lines.append(f"| `{name}` | {title} | not run | not run | not run |")
            else:
                lines.append(
                    f"| `{name}` | {title} | {score['sku_accuracy'] * 100:.1f}% of "
                    f"{score['lines']:.0f} | {score['wrong_sku_high_confidence']:.0f} | "
                    f"${score['cost_usd']:.4f} |"
                )
    lines.append("")

    if ran:
        best = ran[-1]
        lines += [
            f"## Case by case: `{best.name}`",
            "",
            "| RFQ | Set | What makes it hard | Lines right | Expected | Route | Review reasons |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in best.cases:
            mark = " ⚠️" if c.unsafe_auto_approval else ""
            extra = c.line_pred - c.line_tp
            right = f"{c.line_tp}/{c.n_gold}" + (f" (+{extra} wrong)" if extra else "")
            lines.append(
                f"| {c.id} | {c.split} | {c.hard} | {right} | {c.expected_route} | "
                f"{c.route}{mark} | {', '.join(c.flags) or '-'} |"
            )
        lines += [
            "",
            "⚠️ marks an unsafe auto-approval: approved although a line, the recipient or the "
            "delivery date is wrong, or although policy requires a person.",
            "",
        ]
    return "\n".join(lines)


def run_eval(
    configs: list[str],
    data_dir: Path = DATA_DIR,
    *,
    model: str,
    effort: str,
    live: bool = False,
    record: bool = False,
    concurrency: int = 8,
    log=None,
) -> tuple[str, list[ConfigReport]]:
    gold = load_gold(data_dir)
    catalog = load_catalog(data_dir)
    llm_kw = dict(model=model, effort=effort, live=live, record=record, concurrency=concurrency)

    def kwargs(name: str) -> dict:
        return llm_kw if CONFIGS[name]["mode"] == "llm" else {}

    reports = [
        evaluate_config(
            name, gold, data_dir, workers=concurrency, log=log, catalog=catalog, **kwargs(name)
        )
        for name in configs
    ]
    matcher_scores: dict[str, dict[str, dict | str]] = {}
    for name in configs:
        matcher = build_pipeline(
            **CONFIGS[name], data_dir=data_dir, catalog=catalog, **kwargs(name)
        ).matcher
        if matcher.name in matcher_scores:
            continue
        groups = matcher_scores[matcher.name] = {}
        for title, splits in MATCHER_GROUPS.items():
            cases = [g for g in gold if g.split in splits]
            if not cases:
                continue
            try:
                groups[title] = score_matcher(matcher, cases, concurrency, log)
            except CassetteMiss as exc:
                groups[title] = str(exc)
    return render_report(reports, matcher_scores, gold, model, effort), reports


def results_json(reports: list[ConfigReport]) -> str:
    out = {}
    for r in reports:
        if r.skipped:
            out[r.name] = {"skipped": r.skipped}
        else:
            out[r.name] = {s: r.metrics(s) for s in SPLITS if r.split(s)}
    return json.dumps(out, indent=2)
