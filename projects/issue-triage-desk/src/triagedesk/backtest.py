"""The backtest is a replay: stored issues go through `process_issue` in the order they
were opened. Training and the taxonomy see issues opened before the cut date only.

The search index holds every stored issue, but each search is limited to issues
opened before the one being answered, so the replay sees what the desk would have
seen on that day. One thing it cannot undo: the tracker returns a title and body as
they are now, and a few were edited after they were submitted.
"""

from __future__ import annotations

import hashlib
import json
import pickle
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

from .budget import Budget
from .classifier import LabelClassifier
from .config import Settings, day_start
from .embed import Embedder
from .index import IssueIndex
from .llm import LLMProvider
from .log import PredictionLog
from .models import Issue, LabelInfo
from .pipeline import CONFIGS, PROMPT_OF, Desk, find_candidates, process_issue
from .rules import Rules
from .store import Store
from .taxonomy import build_taxonomy
from .triage import Triager

Progress = Callable[[str], None]


def _quiet(line: str) -> None:
    pass


def training_issues(store: Store, settings: Settings, cut: date) -> list[Issue]:
    return store.issues(day_start(settings.history_start), day_start(cut))


def window(store: Store, start: date, end: date) -> list[Issue]:
    """Issues opened from `start` to `end`, both days included, oldest first."""
    return store.issues(day_start(start), day_start(end) + timedelta(days=1))


def _fit_classifier(
    settings, taxonomy, training, cut, progress: Progress, cache: bool = True
) -> LabelClassifier:
    """Train, or load the classifier trained earlier on exactly these issues."""
    if not cache:
        return LabelClassifier(taxonomy).fit(training)
    fingerprint = json.dumps(
        [settings.repo, cut.isoformat(), len(training), training[-1].number if training else 0,
         taxonomy.names()]
    )  # fmt: skip
    key = hashlib.sha256(fingerprint.encode()).hexdigest()[:12]
    path = settings.home / "var" / f"classifier-{key}.pkl"
    if path.exists():
        with open(path, "rb") as handle:
            return pickle.load(handle)
    progress(f"training the classifier on {len(training)} issues")
    classifier = LabelClassifier(taxonomy).fit(training)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        pickle.dump(classifier, handle)
    return classifier


def build_desk(
    settings: Settings,
    store: Store,
    embedder: Embedder,
    *,
    cut: date,
    provider: LLMProvider | None = None,
    budget: Budget | None = None,
    progress: Progress = _quiet,
    cache: bool = True,
) -> Desk:
    """Taxonomy, classifier, rules and index, from issues opened before `cut`."""
    training = training_issues(store, settings, cut)
    if not training:
        raise ValueError(f"No stored issues before {cut}. Run `triagedesk backfill` first.")
    raw_labels = store.get_state("labels")
    infos = (
        [LabelInfo.model_validate(item) for item in json.loads(raw_labels)] if raw_labels else []
    )
    taxonomy = build_taxonomy(settings, training, infos)
    classifier = _fit_classifier(settings, taxonomy, training, cut, progress, cache)
    index = IssueIndex(store, embedder)
    added = index.embed_missing(progress)
    if added:
        progress(f"embedded {added} issues")
    index.load()
    triagers = {}
    if provider is not None:
        triagers = {
            "model": Triager(provider, settings, taxonomy, budget, with_context=True),
            "model-alone": Triager(provider, settings, taxonomy, budget, with_context=False),
        }
    return Desk(settings, taxonomy, store, index, Rules(settings, taxonomy), classifier, triagers)


def prefetch(
    desk: Desk,
    issues: list[Issue],
    variant: str,
    *,
    concurrency: int = 4,
    progress: Progress = _quiet,
) -> dict:
    """Ask the model about many issues at once, so the replay finds every answer recorded.

    The replay itself runs in date order, one issue at a time. Doing the paid calls
    here first, a few at a time, changes how long the run takes and nothing else.
    """
    triager = desk.triagers[variant]
    settings = desk.settings

    # The searches use the store, which belongs to this thread. Only the model call,
    # which touches nothing shared but the recording, runs in the pool.
    jobs: list[tuple[Issue, list[Issue]]] = []
    for issue in issues:
        earlier: list[Issue] = []
        if triager.with_context:
            candidates = find_candidates(
                desk.index, issue, settings.candidates, settings.retrieval, settings.fusion_k
            )
            earlier = [desk.store.issue(c.number) for c in candidates[: settings.model_candidates]]
        jobs.append((issue, earlier))

    def one(job: tuple[Issue, list[Issue]]) -> bool:
        return triager.triage(*job).triage is not None

    answered = failed = 0
    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for done, ok in enumerate(pool.map(one, jobs), start=1):
            answered += ok
            failed += not ok
            if done % 100 == 0 or done == len(issues):
                progress(
                    f"{variant}: {done} of {len(issues)} issues, {failed} without an answer, "
                    f"US${triager.cost_usd:.3f} spent"
                )
    return {"answered": answered, "failed": failed, "cost_usd": triager.cost_usd}


def replay(
    desk: Desk,
    issues: list[Issue],
    configs: tuple[str, ...],
    log: PredictionLog,
    *,
    now: datetime | None = None,
    progress: Progress = _quiet,
) -> dict:
    """Run issues through the desk in date order and append the answers to the log."""
    now = now or datetime.now(UTC)
    issues = sorted(issues, key=lambda i: (i.created_at, i.number))
    written = skipped = 0
    for config in configs:
        if config not in CONFIGS:
            raise ValueError(f"Unknown configuration: {config}")
        if config in PROMPT_OF and PROMPT_OF[config] not in desk.triagers:
            raise ValueError(f"Configuration {config} needs a model provider.")
        version = desk.version(config)
        batch = []
        for issue in issues:
            if log.has(issue.number, config, version):
                skipped += 1
                continue
            prediction, note = process_issue(desk, issue, config, now=now)
            if note:
                desk.store.set_note(issue.number, version, note)
            batch.append(prediction)
        written += log.append(batch)
        progress(f"{config} ({version}): {len(batch)} predictions")
    return {"written": written, "skipped": skipped}
