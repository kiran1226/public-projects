"""Score the matching step on data this project's author did not write.

Every other evaluation here runs on requests and labels written for the project.
This one takes a public product-matching benchmark, Billiger.de Products, and puts
its offers through the unchanged matching step: search, then choose or decline.

The data is not in the repository. `fetch` downloads one pinned file and checks its
checksum; recorded model responses keep the decision and drop the free-text reason.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from math import comb
from pathlib import Path
from typing import Literal

from .catalog import Catalog, Sku, normalize
from .evals import Progress
from .llm import (
    PRICES,
    AnthropicProvider,
    CassetteMiss,
    CassetteProvider,
    LLMError,
    LLMProvider,
)
from .match import LLMMatcher, Matcher, TopHitMatcher
from .models import Confidence, LineItem, Usage
from .pipeline import DATA_DIR

REPO = "wbsg-uni-mannheim/billiger-de-products"
COMMIT = "1452caee94847554c6e4a2328f7ece05f98bd05c"
FILE = "data/solute_de/gold-standards_adjusted/products80cc20rnd050un_gs.json.gz"
SHA256 = "a6f703bf3e454587316eb00e509700723a563230e195be18cb4c823176f7b5f3"
URL = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/{FILE}"
DATA_PATH = DATA_DIR / "external" / "billiger" / Path(FILE).name
CASSETTE_DIR = DATA_DIR / "cassettes"

# From the dataset's reports/final_results/summary.csv at the pinned commit: German,
# 80% corner cases, Half-Seen test set. Supervised systems use the large training set.
PUBLISHED = [
    ("Word co-occurrence baseline, trained", 31.78, 42.18, 36.25),
    ("XLM-R, trained (best trained system)", 43.19, 88.30, 58.01),
    ("GPT-5.2 zero-shot, simple prompt", 53.62, 89.68, 67.11),
    ("GPT-5.2 zero-shot, rule-guided prompt", 94.54, 66.37, 77.99),
]

Kind = Literal["correct", "wrong", "missed", "correct_decline"]


class SpendCapReached(LLMError):
    """The run has spent its allowance on live calls. Recordings made so far are kept."""


# --- data --------------------------------------------------------------------


@dataclass(frozen=True)
class Offer:
    id: str
    brand: str
    name: str

    @property
    def title(self) -> str:
        """What a customer would write: the name, with the brand in front if it lacks it."""
        if self.brand and self.brand.lower() not in self.name.lower():
            return f"{self.brand} {self.name}"
        return self.name


@dataclass(frozen=True)
class Query:
    """One offer to match, the offers it was paired with, and which of those are matches."""

    offer: Offer
    candidates: tuple[Offer, ...]
    gold: frozenset[str]


def _offer(row: dict, side: str) -> Offer:
    return Offer(
        id=str(row[f"id_{side}"]),
        brand=(row.get(f"brand_{side}") or "").strip(),
        name=(row.get(f"name_{side}") or "").strip(),
    )


def load_queries(path: Path) -> list[Query]:
    """Group the labelled pairs by their left offer, in file order.

    A match is what the `label` field says. Two offers with the same product id are
    not always a match: the dataset's audit relabelled some of them.
    """
    offers: dict[str, Offer] = {}
    pools: dict[str, dict[str, Offer]] = {}
    gold: dict[str, set[str]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            left, right = _offer(row, "left"), _offer(row, "right")
            offers.setdefault(left.id, left)
            pools.setdefault(left.id, {}).setdefault(right.id, right)
            if row["label"] == 1:
                gold.setdefault(left.id, set()).add(right.id)
    return [
        Query(offers[qid], tuple(pool.values()), frozenset(gold.get(qid, ())))
        for qid, pool in pools.items()
    ]


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https URL
        return response.read()


def fetch(
    dest: Path = DATA_PATH,
    *,
    download: Callable[[str], bytes] = _download,
    sha256: str = SHA256,
) -> Path:
    """Download the pinned test file. Nothing is written unless the checksum matches."""
    data = download(URL)
    digest = hashlib.sha256(data).hexdigest()
    if digest != sha256:
        raise ValueError(
            f"checksum mismatch for {URL}: expected {sha256}, got {digest}. Nothing was saved."
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return dest


# --- one query ---------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    query_id: str
    kind: Kind
    confidence: Confidence
    n_gold: int  # matching offers among the candidates, according to the labels
    gold_shown: bool  # the search step retrieved at least one of them
    failed: bool  # the model call failed; counted as no pick
    usage: Usage
    # A wrong pick whose title is, word for word, the query's: the labels separate two
    # offers that a title cannot tell apart.
    same_title: bool = False


def pool_catalog(query: Query) -> Catalog:
    """The query's candidates as a catalog of their own. Titles only; no prices are known."""
    return Catalog.from_skus(
        Sku(sku=c.id, category="", description=c.title, keywords="", unit="pcs",
            unit_cost=0, list_price=0, moq=1, lead_time_days=0)
        for c in query.candidates
    )  # fmt: skip


def run_query(query: Query, make_matcher: Callable[[Catalog], Matcher]) -> Outcome:
    catalog = pool_catalog(query)
    matcher = make_matcher(catalog)
    item = LineItem(description=query.offer.title, quantity=None, unit=None)
    shown = {s.sku for s, _ in catalog.search(item.description, getattr(matcher, "limit", 8))}
    usage, failed = Usage(), False
    try:
        match = matcher.match(item, usage)
        sku, confidence = match.sku, match.confidence
    except (CassetteMiss, SpendCapReached):
        raise  # setup problems, not results
    except LLMError:
        # As in the pipeline: a failed match call leaves the line for a person.
        sku, confidence, failed = None, "low", True
    if sku is None:
        kind: Kind = "missed" if query.gold else "correct_decline"
    else:
        kind = "correct" if sku in query.gold else "wrong"
    picked = {c.id: c for c in query.candidates}.get(sku) if kind == "wrong" else None
    return Outcome(
        query_id=query.offer.id,
        kind=kind,
        confidence=confidence,
        n_gold=len(query.gold),
        gold_shown=bool(shown & query.gold),
        failed=failed,
        usage=usage,
        same_title=bool(picked) and normalize(picked.title) == normalize(item.description),
    )


def calls_needed(queries: list[Query]) -> int:
    """Queries that reach the model: those for which the search retrieves a candidate."""
    return sum(bool(pool_catalog(q).search(q.offer.title, 1)) for q in queries)


# --- scoring -----------------------------------------------------------------


def _prf(correct: int, picks: int, matches: int) -> tuple[float, float, float]:
    precision = correct / picks if picks else 0.0
    recall = correct / matches if matches else 0.0
    total = precision + recall
    return precision, recall, (2 * precision * recall / total if total else 0.0)


def score(outcomes: list[Outcome]) -> dict[str, float]:
    """Counts per query, and precision, recall and F1 over the labelled pairs.

    A pick is a predicted match for that pair; every other pair of the query is a
    predicted non-match. The matcher picks at most one offer per query.
    """

    def count(*kinds: str, high: bool = False) -> int:
        return sum(o.kind in kinds and (o.confidence == "high" or not high) for o in outcomes)

    matches = sum(o.n_gold for o in outcomes)
    with_match = sum(o.n_gold > 0 for o in outcomes)
    correct, wrong = count("correct"), count("wrong")
    precision, recall, f1 = _prf(correct, correct + wrong, matches)
    correct_high, wrong_high = count("correct", high=True), count("wrong", high=True)
    precision_high, recall_high, f1_high = _prf(correct_high, correct_high + wrong_high, matches)
    usage = Usage()
    for o in outcomes:
        usage.add(o.usage)
    return {
        "queries": len(outcomes),
        "with_match": with_match,
        "without_match": len(outcomes) - with_match,
        "matches": matches,
        "correct": correct,
        "wrong": wrong,
        "correct_high": correct_high,
        "wrong_high": wrong_high,
        "wrong_high_same_title": sum(
            o.kind == "wrong" and o.confidence == "high" and o.same_title for o in outcomes
        ),
        "missed": count("missed"),
        "missed_not_shown": sum(o.kind == "missed" and not o.gold_shown for o in outcomes),
        "correct_declines": count("correct_decline"),
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "precision_high": precision_high,
        "recall_high": recall_high,
        "f1_high": f1_high,
        "failed_calls": sum(o.failed for o in outcomes),
        "llm_calls": usage.llm_calls,
        "cost_usd": usage.cost_usd,
        "latency_s": usage.latency_s,
    }


# --- a whole run -------------------------------------------------------------


class CappedProvider:
    """Refuses further live calls once `max_usd` has been spent in this run."""

    def __init__(self, inner: LLMProvider, max_usd: float):
        self.inner, self.model, self.max_usd = inner, inner.model, max_usd
        self.spent = 0.0
        self._lock = threading.Lock()

    def structured(self, *, system, user, schema):
        with self._lock:
            if self.spent >= self.max_usd:
                raise SpendCapReached(
                    f"Spend cap of ${self.max_usd:.2f} reached after ${self.spent:.4f}. "
                    "Responses recorded so far are kept; run again with a higher --max-usd "
                    "to continue from there."
                )
        parsed, stats = self.inner.structured(system=system, user=user, schema=schema)
        with self._lock:
            self.spent += stats.cost_usd
        return parsed, stats


def _drop_reason(output: dict) -> dict:
    return {**output, "reason": ""}


def _outcomes(name: str, queries, make_matcher, workers: int, log) -> list[Outcome]:
    progress = Progress(f"{name} on outside data: queries", len(queries), 50, log)
    with ThreadPoolExecutor(max(1, workers)) as pool:
        return list(pool.map(progress.wrap(lambda q: run_query(q, make_matcher)), queries))


def _run(name: str, queries, make_matcher, workers: int, log) -> dict[str, float]:
    return score(_outcomes(name, queries, make_matcher, workers, log))


def _recording(model: str, effort: str, cassette_dir: Path, **kw) -> CassetteProvider:
    """The recorded responses of one model. Read only unless `inner` and `record` are given."""
    reference = AnthropicProvider(model, effort=effort)  # for the request parameters only
    return CassetteProvider(
        cassette_dir / f"outside-{reference.model}.json",
        reference.model,
        params={"effort": reference.effort, "max_tokens": reference.max_tokens},
        scrub=_drop_reason,
        **kw,
    )


def run_outside(
    queries: list[Query],
    *,
    model: str,
    effort: str,
    live: bool = False,
    record: bool = False,
    concurrency: int = 8,
    max_usd: float = 1.0,
    cassette_dir: Path = CASSETTE_DIR,
    live_provider: LLMProvider | None = None,
    log=None,
) -> dict[str, dict[str, float] | str]:
    """Scores per matcher. A matcher that could not run maps to the reason instead."""
    results: dict[str, dict[str, float] | str] = {
        "top-hit": _run("top-hit", queries, TopHitMatcher, concurrency, log)
    }
    inner = None
    if live or record:
        live_provider = live_provider or AnthropicProvider(
            model, effort=effort, concurrency=concurrency
        )
        inner = CappedProvider(live_provider, max_usd)
    provider = _recording(model, effort, cassette_dir, inner=inner, record=record)
    try:
        results["llm-match"] = _run(
            "llm-match", queries, lambda catalog: LLMMatcher(catalog, provider), concurrency, log
        )
    except CassetteMiss as exc:
        results["llm-match"] = str(exc)
    return results


# --- several recorded models -------------------------------------------------


def recorded_models(cassette_dir: Path = CASSETTE_DIR) -> list[str]:
    """The models that have a recording for this benchmark, cheapest first."""
    names = [p.stem.removeprefix("outside-") for p in cassette_dir.glob("outside-*.json")]
    return sorted(names, key=lambda name: (PRICES.get(name, (float("inf"),))[0], name))


def mcnemar_exact(only_first: int, only_second: int) -> float:
    """Two-sided exact McNemar test on the queries two matchers disagree on.

    If both were equally good, each such query would be a coin toss. The result is the
    chance of a split at least this uneven.
    """
    n, k = only_first + only_second, min(only_first, only_second)
    return min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2**n)


def _right(outcome: Outcome) -> bool:
    return outcome.kind in ("correct", "correct_decline")


def compare_models(
    queries: list[Query],
    *,
    against: str,
    effort: str,
    cassette_dir: Path = CASSETTE_DIR,
    concurrency: int = 8,
    log=None,
) -> dict:
    """Every recorded model replayed on the same queries. Makes no live calls.

    A model whose recording does not cover these queries is left out. Each other model
    is also compared with `against` query by query.
    """
    outcomes: dict[str, list[Outcome]] = {}
    for model in recorded_models(cassette_dir):
        provider = _recording(model, effort, cassette_dir)
        try:
            outcomes[model] = _outcomes(
                f"{model}, recorded",
                queries,
                lambda catalog, provider=provider: LLMMatcher(catalog, provider),
                concurrency,
                log,
            )
        except CassetteMiss:
            continue
    paired = {}
    for model, theirs in outcomes.items():
        if model == against or against not in outcomes:
            continue
        pairs = list(zip(theirs, outcomes[against], strict=True))
        only_this = sum(_right(a) and not _right(b) for a, b in pairs)
        only_other = sum(_right(b) and not _right(a) for a, b in pairs)
        paired[model] = {
            "only_this_right": only_this,
            "only_other_right": only_other,
            "p": mcnemar_exact(only_this, only_other),
        }
    return {
        "models": {model: score(found) for model, found in outcomes.items()},
        "against": against,
        "paired": paired,
    }


def _share(part: int, whole: int) -> str:
    return f"{part} of {whole}" + (f" ({part / whole * 100:.1f}%)" if whole else "")


_MODEL_ROWS = [
    (
        "Picks that are right (precision)",
        lambda s: _share(s["correct"], s["correct"] + s["wrong"]),
    ),
    ("Matches found (recall)", lambda s: _share(s["correct"], s["matches"])),
    ("F1, any pick", lambda s: f"{s['f1'] * 100:.1f}%"),
    (
        "High-confidence picks that are wrong",
        lambda s: _share(s["wrong_high"], s["correct_high"] + s["wrong_high"]),
    ),
    (
        "Picked an offer although no candidate matches",
        lambda s: f"{s['without_match'] - s['correct_declines']} of {s['without_match']}",
    ),
    ("Declined although a match was among the candidates", lambda s: f"{s['missed']}"),
    ("Model calls", lambda s: f"{s['llm_calls']}"),
    ("Model cost (USD)", lambda s: f"${s['cost_usd']:.2f}"),
]


def _comparison_section(comparison: dict) -> list[str]:
    models, against = comparison["models"], comparison["against"]
    lines = [
        "## The same queries with each recorded model",
        "",
        "One recorded run per model. The prompt, the search and the effort setting are the "
        "same for all of them; only the model differs.",
        "",
        "| | " + " | ".join(f"`{m}`" for m in models) + " |",
        "|---|" + "---:|" * len(models),
    ]
    for label, cell in _MODEL_ROWS:
        lines.append(f"| {label} | " + " | ".join(cell(s) for s in models.values()) + " |")
    (cheapest, low), *_, (dearest, high) = models.items()

    def wrong_share(s: dict) -> float:
        picks = s["correct_high"] + s["wrong_high"]
        return s["wrong_high"] / picks * 100 if picks else 0.0

    lines += [
        "",
        f"From `{cheapest}` to `{dearest}`, recall goes from {low['recall'] * 100:.1f}% to "
        f"{high['recall'] * 100:.1f}%, and the share of high-confidence picks that are wrong "
        f"from {wrong_share(low):.1f}% to {wrong_share(high):.1f}%. In the pipeline only a "
        "high-confidence pick can go out without a person, so that share is the number that "
        "bears on safety. Recall says how much of the matching a model does on its own.",
        "",
    ]
    if comparison["paired"]:
        lines += [
            f"Query by query against `{against}`. A query counts as right when the right "
            "offer was picked or the matcher correctly declined.",
            "",
            f"| Model | Right where `{against}` is wrong | Wrong where `{against}` is right | "
            "Chance of a split this uneven | Reading |",
            "|---|---:|---:|---:|---|",
        ]
        for model, pair in comparison["paired"].items():
            reading = "unlikely to be chance alone" if pair["p"] < 0.05 else "could be chance"
            lines.append(
                f"| `{model}` | {pair['only_this_right']} | {pair['only_other_right']} | "
                f"{pair['p']:.2g} | {reading} |"
            )
        lines += [
            "",
            "The chance is from a two-sided exact McNemar test: if the two models were "
            "equally good, how often the queries they disagree on would split at least this "
            "unevenly. Under 0.05 the table reads the difference as unlikely to be chance "
            "alone; that line is a convention. The test treats the queries as independent "
            "and says nothing about how much a second run of the same model would differ.",
            "",
        ]
    return lines


# --- report ------------------------------------------------------------------

_ROWS = [
    ("Right offer picked", lambda s: f"{s['correct']} of {s['with_match']}"),
    ("Wrong offer picked", lambda s: f"{s['wrong']}"),
    ("of which with high confidence", lambda s: f"{s['wrong_high']}"),
    (
        "of those, the picked offer's title is word for word the query's",
        lambda s: f"{s['wrong_high_same_title']}",
    ),
    ("Declined although a match was among the candidates", lambda s: f"{s['missed']}"),
    ("of which the search did not show the match", lambda s: f"{s['missed_not_shown']}"),
    (
        "Declined correctly, no match among the candidates",
        lambda s: f"{s['correct_declines']} of {s['without_match']}",
    ),
    ("Precision, any pick", lambda s: f"{s['precision'] * 100:.1f}%"),
    ("Recall, any pick", lambda s: f"{s['recall'] * 100:.1f}%"),
    ("F1, any pick", lambda s: f"{s['f1'] * 100:.1f}%"),
    ("Precision, high-confidence picks only", lambda s: f"{s['precision_high'] * 100:.1f}%"),
    ("Recall, high-confidence picks only", lambda s: f"{s['recall_high'] * 100:.1f}%"),
    ("F1, high-confidence picks only", lambda s: f"{s['f1_high'] * 100:.1f}%"),
    ("Model calls", lambda s: f"{s['llm_calls']}"),
    ("Model calls that failed, counted as declined", lambda s: f"{s['failed_calls']}"),
    ("Model cost (USD)", lambda s: f"${s['cost_usd']:.2f}"),
    ("Model time, summed over calls (s)", lambda s: f"{s['latency_s']:.0f}"),
]


def render_report(
    results: dict[str, dict[str, float] | str],
    queries: list[Query],
    *,
    total_queries: int,
    model: str,
    effort: str,
    comparison: dict | None = None,
    **_,
) -> str:
    """`comparison` is the result of `compare_models`; shown when it holds several models."""
    several_models = bool(comparison) and len(comparison["models"]) > 1
    pairs = sum(len(q.candidates) for q in queries)
    matches = sum(len(q.gold) for q in queries)
    with_match = sum(bool(q.gold) for q in queries)
    several = sum(len(q.gold) > 1 for q in queries)
    sizes = [len(q.candidates) for q in queries]
    partial = len(queries) < total_queries
    scope = (
        f"The first {len(queries)} of {total_queries} queries: a partial run, not a result."
        if partial
        else f"All {len(queries)} queries."
    )
    names = list(results)
    lines = [
        "# Evaluation on outside data",
        "",
        f"Generated {date.today().isoformat()} by `rfq2quote eval-outside`. LLM matching uses "
        f"`{model}` at `{effort}` effort.",
        "",
        "Every other number in this project comes from requests and labels written by the "
        "project's author. This report scores one step, matching a line of text to a product "
        "or declining, on product offers and labels that someone else produced.",
        "",
        "## The data",
        "",
        "| | |",
        "|---|---|",
        "| Dataset | Billiger.de Products v1.1, German. Steiner, Elagin, Peeters, Knopp and "
        "Bizer, University of Mannheim, [arXiv:2609.37713](https://arxiv.org/abs/2609.37713) |",
        f"| Source | [github.com/{REPO}](https://github.com/{REPO}) at commit `{COMMIT[:12]}` |",
        f"| File | `{FILE}`, the configuration its authors recommend for a single evaluation: "
        "80% corner cases, half of the products unseen in training |",
        f"| SHA-256 | `{SHA256}` |",
        "| Content | Real offers from the price comparison portal billiger.de. The labels "
        "(same product or not) come from the dataset's authors |",
        f"| Scored here | {scope} {pairs} labelled pairs, {matches} of them matches. "
        f"{with_match} queries have a match among their candidates |",
        "",
        "The data is not in this repository. Its page states no licence, only that it is "
        '"released with the permission of solute GmbH, the operator of billiger.de", so '
        "`rfq2quote fetch-outside` downloads the pinned file and checks its checksum. The "
        "recorded model responses keep the chosen offer id and the confidence; the model's "
        "free-text reason is not stored.",
        "",
        "## How it is scored",
        "",
        "- Each left-hand offer in the file is treated as a customer line. The right-hand "
        f"offers it is paired with are its candidates ({min(sizes)} to {max(sizes)} per query), "
        "loaded into a catalog of their own.",
        "- The matching step runs unchanged: the same search, the same prompt (`match_v1`), "
        "the same rule that an answer outside the candidates is discarded. It returns one "
        "offer or declines.",
        "- Only titles are used: the name, with the brand in front when the name lacks it. "
        "Descriptions and prices are shown neither to the search nor to the model. These "
        "choices were fixed before the first model call.",
        "- A pick is right when the dataset labels that pair a match. Precision, recall and "
        "F1 are counted over the labelled pairs: a pick is a predicted match, every other "
        "pair a predicted non-match.",
        f"- The matcher picks at most one offer per query and {several} queries have more "
        f"than one matching offer, so recall cannot exceed "
        f"{with_match / matches * 100 if matches else 0:.1f}%.",
        "- In the pipeline only a high-confidence match can be auto-approved; every other "
        "pick is shown to a person. The high-confidence rows are therefore the ones that "
        "describe what would go out unchecked.",
        "",
        "## Results",
        "",
        scope,
        "",
        "| | " + " | ".join(f"`{n}`" for n in names) + " |",
        "|---|" + "---:|" * len(names),
    ]
    for label, cell in _ROWS:
        cells = [cell(r) if isinstance(r, dict) else "not recorded" for r in results.values()]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    for name, result in results.items():
        if isinstance(result, str):
            lines += [f"> `{name}` was **not run**: {result}", ""]
    lines += [
        "`top-hit` is the no-model baseline: it always takes the best search hit, and calls "
        "it high confidence when every number in the query appears in the candidate.",
        "",
    ]
    if several_models:
        lines += _comparison_section(comparison)
    lines += [
        "## Published results on the same file",
        "",
        "From the dataset's own `reports/final_results/summary.csv` (German, 80% corner "
        "cases, Half-Seen).",
        "",
        "| System | Precision | Recall | F1 |",
        "|---|---:|---:|---:|",
    ]
    lines += [f"| {name} | {p:.2f}% | {r:.2f}% | {f:.2f}% |" for name, p, r, f in PUBLISHED]
    lines += [
        "",
        "These are context, not a ranking. Those systems classify one pair at a time and see "
        "brand, name, description and price. The matcher here sees a query's candidates "
        "together, sees titles only, and is built to decline when it is not sure.",
        "",
        "## How far to trust this",
        "",
        (
            "- **One run per model.** There is no variance estimate: a second run could move "
            "each count by a few picks."
            if several_models
            else "- **One run of one model.** There is no variance estimate."
        ),
        "- **Consumer products, not industrial materials.** This shows how the matching step "
        "behaves on text its author never saw. It says nothing about extraction, pricing or "
        "the guardrails.",
        "- **The prompt was not adapted.** It was written for cables and transformers and "
        "names their attributes.",
        "- **The search step is barely tested.** Candidate pools are small. Finding the "
        "right offer in a full catalog would be harder.",
        "- **Training-data overlap is unknown.** The dataset was published in 2026; whether "
        "the model has seen any of it cannot be checked from here.",
        "- **The labels are taken as given.** Their authors document an audit of the test "
        "sets; this project did not re-check them.",
        "",
    ]
    return "\n".join(lines)
