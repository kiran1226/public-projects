"""The backtest: replay stored history, one day at a time, through the live pipeline.

Profiles are built from awards published before the cut date and are then frozen.
Each test day's tenders are ranked with nothing later in view. What the suppliers
went on to win is collected separately and joined only when scoring.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from .awards import SupplierIndex, wins_by_supplier
from .config import CUT_DATE, PROFILE_START, TEST_END, Settings
from .embed import DEFAULT_MODEL, CachedEmbedder, Embedder, FastEmbedder
from .log import PredictionLog, system_version
from .models import Notice, Outcome, PastWin, Profile, Shortlist
from .panel import Panel, panel_outcomes, select_panel
from .pipeline import Assess, process_day
from .rank import (
    AlertRanker,
    BuyerRanker,
    CpvRanker,
    EmbeddingRanker,
    FusedRanker,
    LexicalRanker,
    Ranker,
)
from .store import Store

ALERT_DEPTH = 5
# A tender is compared with the single past win it most resembles. Chosen on the
# development months: the nearest win did better than the mean of the nearest three.
TOP_TEXTS = 1
FUSION_K = 20
# The lexical ranker learns word weights from this many past award texts.
LEXICAL_CORPUS_SIZE = 40_000
MODEL_RANKER = "fused+model"


@dataclass
class World:
    """Everything the backtest needs, loaded once."""

    index: SupplierIndex
    wins: dict[str, dict[str, PastWin]]
    test_procedures: dict[str, Notice]
    panel: Panel
    outcomes: list[Outcome]
    history_texts: list[str]
    awards_loaded: int
    suppliers: int

    def notices_by_day(self) -> dict[date, list[Notice]]:
        days: dict[date, list[Notice]] = defaultdict(list)
        for notice in self.test_procedures.values():
            days[notice.publication_date].append(notice)
        return dict(sorted(days.items()))


def load_world(
    store: Store,
    *,
    cut: date = CUT_DATE,
    test_end: date = TEST_END,
    profile_start: date = PROFILE_START,
    today: date | None = None,
    panel_size: int = 200,
    progress: Callable[[str], None] = lambda _line: None,
) -> World:
    today = today or date.today()
    awards = store.awards(profile_start, today)
    progress(f"awards loaded: {len(awards)}")
    index = SupplierIndex.build(awards)
    wins = wins_by_supplier(awards, index)
    progress(
        f"suppliers: {len(wins)}, numbers shared by unrelated names: {index.shared_identifiers}"
    )
    test_procedures = {n.procedure_id: n for n in store.new_procedures(cut, test_end)}
    progress(f"tenders first published {cut} to {test_end}: {len(test_procedures)}")
    panel = select_panel(wins, index, cut, test_procedures, size=panel_size)
    outcomes = panel_outcomes(panel, wins, cut, test_procedures)
    progress(
        f"panel: {len(panel.profiles)} of {panel.eligible} eligible suppliers, "
        f"{len(outcomes)} tenders they went on to win"
    )
    # Word weights come from award texts published before the cut: history only.
    history = [a.text for a in awards if a.publication_date < cut and a.text]
    step = max(1, len(history) // LEXICAL_CORPUS_SIZE)
    return World(
        index=index,
        wins=wins,
        test_procedures=test_procedures,
        panel=panel,
        outcomes=outcomes,
        history_texts=history[::step],
        awards_loaded=len(awards),
        suppliers=len(wins),
    )


def make_embedder(settings: Settings) -> Embedder:
    return CachedEmbedder(FastEmbedder(DEFAULT_MODEL), settings.embedding_cache)


def build_rankers(history_texts: Sequence[str] | None, embedder: Embedder) -> list[Ranker]:
    """The rankers, in report order.

    The fused ranker combines weighted categories and multilingual embeddings. Two
    other signals are scored on their own and left out of the fusion, because on the
    development months each made the fused list worse or no better: word matching
    (lexical) and buyer history. The decision log has the numbers.

    The lexical ranker needs a body of past award texts to learn word weights from, so
    the live run, which has no history database, passes None and leaves it out.
    """
    embedding = EmbeddingRanker(embedder, top=TOP_TEXTS)
    cpv_ranker = CpvRanker()
    buyer = BuyerRanker()
    rankers: list[Ranker] = [AlertRanker(depth=ALERT_DEPTH), cpv_ranker]
    if history_texts is not None:
        rankers.append(LexicalRanker(history_texts, top=TOP_TEXTS))
    rankers += [embedding, buyer, FusedRanker([cpv_ranker, embedding], k=FUSION_K)]
    return rankers


def ranker_versions(
    settings: Settings, embedder_name: str, *, model: str = "", effort: str = "", prompt: str = ""
) -> dict[str, str]:
    """A version per ranker. Each covers every setting that can change that ranker's lists."""
    common = dict(
        divisions=settings.divisions, list_size=settings.list_size, cut=CUT_DATE.isoformat()
    )
    text = dict(top=TOP_TEXTS, corpus=LEXICAL_CORPUS_SIZE)
    fused = dict(k=FUSION_K, embedder=embedder_name, parts=["cpv", "embedding"], top=TOP_TEXTS)
    return {
        "alert": system_version(ranker="alert", depth=ALERT_DEPTH, **common),
        "cpv": system_version(ranker="cpv", **common),
        "lexical": system_version(ranker="lexical", **text, **common),
        "embedding": system_version(ranker="embedding", embedder=embedder_name, **text, **common),
        "buyer": system_version(ranker="buyer", **common),
        "fused": system_version(ranker="fused", **fused, **common),
        MODEL_RANKER: system_version(
            ranker=MODEL_RANKER,
            model=model,
            effort=effort,
            prompt=hashlib.sha256(prompt.encode()).hexdigest()[:12],
            assess_top=settings.assess_top,
            **fused,
            **common,
        ),
    }


def model_sample(
    outcomes: Sequence[Outcome], profiles: int, days_per_profile: int
) -> set[tuple[str, date]]:
    """Profile-days the model is run on in the backtest: a fixed, repeatable sample.

    Running the model on every profile-day would cost far more than the question is
    worth. The sample takes the first `profiles` panel suppliers by id and, for each,
    up to `days_per_profile` days on which a tender it later won was published. The
    free rankers are scored on the same profile-days for a like-for-like comparison.
    """
    by_profile: dict[str, list[date]] = defaultdict(list)
    for outcome in outcomes:
        if outcome.day is not None and not outcome.filter_hits:
            by_profile[outcome.profile_id].append(outcome.day)
    sample: set[tuple[str, date]] = set()
    for profile_id in sorted(by_profile)[:profiles]:
        for day in sorted(set(by_profile[profile_id]))[:days_per_profile]:
            sample.add((profile_id, day))
    return sample


def replay(
    world: World,
    rankers: Sequence[Ranker],
    versions: dict[str, str],
    log: PredictionLog,
    *,
    start: date,
    end: date,
    list_size: int,
    assess: Assess | None = None,
    assess_top: int = 10,
    assess_on: set[tuple[str, date]] | None = None,
    now: datetime | None = None,
    progress: Callable[[str], None] = lambda _line: None,
) -> int:
    """Run every day in [start, end] through the pipeline, oldest first. Returns lists written."""
    now = now or datetime.now(UTC)
    written = 0
    for day, notices in world.notices_by_day().items():
        if not start <= day <= end:
            continue
        lists: list[Shortlist] = process_day(
            day,
            notices,
            world.panel.profiles,
            rankers,
            versions=versions,
            mode="backtest",
            now=now,
            list_size=list_size,
            already_written=log.has,
        )
        if assess is not None and assess_on:
            chosen = [p for p in world.panel.profiles if (p.profile_id, day) in assess_on]
            if chosen:
                fused = [r for r in rankers if r.name == "fused"]
                lists += [
                    s
                    for s in process_day(
                        day,
                        notices,
                        chosen,
                        fused,
                        versions=versions,
                        mode="backtest",
                        now=now,
                        list_size=list_size,
                        assess=assess,
                        assess_ranker="fused",
                        assess_top=assess_top,
                        assess_name=MODEL_RANKER,
                        already_written=log.has,
                    )
                    if s.ranker == MODEL_RANKER
                ]
        added = log.append(lists)
        written += added
        progress(f"{day}: {len(notices)} tenders, {added} lists written")
    return written


def profiles_by_id(profiles: Sequence[Profile]) -> dict[str, Profile]:
    return {profile.profile_id: profile for profile in profiles}
