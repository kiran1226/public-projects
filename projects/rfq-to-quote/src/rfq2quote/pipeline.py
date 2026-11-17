"""The agent: a fixed pipeline with LLM calls only where language needs interpreting.

    intake -> extract (LLM) -> match (search + LLM) -> quote -> guardrails -> route

No request is dropped. An unreadable file or a failed extraction is routed to a
person with the reason; a failed match call leaves that one line unmatched.
"""

from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from .catalog import Catalog
from .extract import Extractor, LLMExtractor, RuleExtractor
from .guardrails import Policy, evaluate, route_for
from .intake import Document, load_document
from .llm import DEFAULT_MODEL, AnthropicProvider, CassetteMiss, CassetteProvider, LLMError
from .match import LLMMatcher, Matcher, TopHitMatcher, no_match
from .models import (
    RFQ,
    Flag,
    MatchedLine,
    PipelineResult,
    Provenance,
    Quote,
    SourceFacts,
    Usage,
)
from .quote import build_quote

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
# One model call per line: cap it so a 500-line tender cannot run up a bill unnoticed.
MAX_MATCHED_LINES = 80


class Pipeline:
    def __init__(
        self,
        extractor: Extractor,
        matcher: Matcher,
        catalog: Catalog,
        policy: Policy | None = None,
        max_workers: int = 1,
    ):
        # Lines of one request are matched concurrently when max_workers > 1.
        self.max_workers = max_workers
        self.extractor = extractor
        self.matcher = matcher
        self.catalog = catalog
        self.policy = policy or Policy()

    def provenance(self, doc_sha256: str = "") -> Provenance:
        return Provenance(
            doc_sha256=doc_sha256,
            extractor=self.extractor.name,
            matcher=self.matcher.name,
            model=getattr(getattr(self.extractor, "provider", None), "model", None),
            prompt_sha256=getattr(self.extractor, "prompt_sha256", None),
            catalog_sha256=self.catalog.sha256,
            policy=self.policy.as_dict(),
        )

    def run_file(self, path: Path, as_of: date | None = None) -> PipelineResult:
        """Like `run`, but a file that cannot be read becomes a review case, not a crash."""
        try:
            doc = load_document(path)
        except Exception as exc:  # noqa: BLE001 - any unreadable input goes to a person
            return self._failure(
                path.stem, str(path), as_of or date.today(), f"Could not read the file: {exc}"
            )
        return self.run(doc, as_of)

    def run(self, doc: Document, as_of: date | None = None) -> PipelineResult:
        """`as_of` is the processing date used for lead-time checks; defaults to today."""
        as_of = as_of or date.today()
        usage = Usage()
        try:
            rfq = self.extractor.extract(doc, usage)
        except CassetteMiss:
            raise  # a setup problem (nothing recorded, live calls off), not a request failure
        except LLMError as exc:
            return self._failure(doc.doc_id, doc.source, as_of, str(exc), doc, usage)

        numbered = list(enumerate(rfq.line_items))
        if self.max_workers > 1 and len(numbered) > 1:
            with ThreadPoolExecutor(self.max_workers) as pool:
                results = list(pool.map(lambda pair: self._match(*pair), numbered))
        else:
            results = [self._match(n, item) for n, item in numbered]
        matches = [match for match, _ in results]
        for _, line_usage in results:
            usage.add(line_usage)
        return price_and_route(
            self.catalog,
            self.policy,
            doc.doc_id,
            doc.source,
            rfq,
            matches,
            as_of,
            usage,
            doc.facts(),
            self.provenance(doc.sha256),
        )

    def _match(self, n: int, item) -> tuple[MatchedLine, Usage]:
        """Match one line with its own usage counter, so lines can run in parallel."""
        usage = Usage()
        if n >= MAX_MATCHED_LINES:
            return no_match(item, f"Over the {MAX_MATCHED_LINES}-line limit."), usage
        try:
            return self.matcher.match(item, usage), usage
        except CassetteMiss:
            raise
        except LLMError as exc:
            return no_match(item, f"Matching failed: {exc}"), usage

    def finish(self, rfq_id, source, rfq, matches, as_of: date, usage: Usage, facts=None):
        return price_and_route(
            self.catalog,
            self.policy,
            rfq_id,
            source,
            rfq,
            matches,
            as_of,
            usage,
            facts,
            self.provenance(),
        )

    def _failure(self, rfq_id, source, as_of, detail, doc=None, usage=None) -> PipelineResult:
        empty = RFQ(
            customer_name=None,
            customer_email=None,
            delivery_date=None,
            delivery_location=None,
            line_items=[],
            missing_info=[],
        )
        return PipelineResult(
            rfq_id=rfq_id,
            source=source,
            as_of=as_of.isoformat(),
            rfq=empty,
            matches=[],
            quote=Quote(),
            flags=[Flag(code="PIPELINE_ERROR", severity="review", detail=detail)],
            route="human_review",
            usage=usage or Usage(),
            facts=doc.facts() if doc else SourceFacts(),
            provenance=self.provenance(doc.sha256 if doc else ""),
        )


def price_and_route(
    catalog: Catalog,
    policy: Policy,
    rfq_id: str,
    source: str,
    rfq: RFQ,
    matches: list[MatchedLine],
    as_of: date,
    usage: Usage,
    facts: SourceFacts | None = None,
    provenance: Provenance | None = None,
) -> PipelineResult:
    """Steps 4 and 5: price, check and route. Also re-checks a reviewer's corrections."""
    quote = build_quote(matches, catalog)
    flags = evaluate(rfq, matches, quote, catalog, as_of, policy, facts)
    return PipelineResult(
        rfq_id=rfq_id,
        source=source,
        as_of=as_of.isoformat(),
        rfq=rfq,
        matches=matches,
        quote=quote,
        flags=flags,
        route=route_for(flags),
        usage=usage,
        facts=facts or SourceFacts(),
        provenance=provenance or Provenance(),
    )


def load_catalog(data_dir: Path = DATA_DIR) -> Catalog:
    path = data_dir / "catalog.csv"
    catalog = Catalog.from_csv(path)
    catalog.sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    return catalog


def build_pipeline(
    mode: str = "llm",
    *,
    prompt_version: str = "v2",
    model: str | None = None,
    effort: str | None = None,
    live: bool = False,
    record: bool = False,
    assisted: bool = False,
    concurrency: int = 8,
    data_dir: Path = DATA_DIR,
    catalog: Catalog | None = None,
) -> Pipeline:
    """`mode` is "llm" (Claude extraction and matching) or "baseline" (rules only).

    Recorded responses are read from `cassettes/<model>.json`, which is also where new
    ones are saved. A set of requests added later keeps its recording in a folder of its
    own, `cassettes/<set>/<model>.json`. Those are read as well and never written, so the
    recording of the original requests stays as it was published.
    """
    catalog = catalog or load_catalog(data_dir)
    policy = Policy(assisted=assisted)
    if mode == "baseline":
        return Pipeline(RuleExtractor(), TopHitMatcher(catalog), catalog, policy)
    if mode != "llm":
        raise ValueError(f"Unknown mode {mode!r}; use 'llm' or 'baseline'.")
    live_provider = AnthropicProvider(model, effort=effort, concurrency=concurrency)
    recordings = data_dir / "cassettes"
    provider = CassetteProvider(
        recordings / f"{live_provider.model}.json",
        live_provider.model,
        inner=live_provider if live or record else None,
        record=record,
        params={"effort": live_provider.effort, "max_tokens": live_provider.max_tokens},
        also=sorted(recordings.glob(f"*/{live_provider.model}.json")),
    )
    return Pipeline(
        LLMExtractor(provider, prompt_version),
        LLMMatcher(catalog, provider),
        catalog,
        policy,
        max_workers=concurrency,
    )


__all__ = ["DEFAULT_MODEL", "Pipeline", "build_pipeline", "load_catalog", "price_and_route"]
