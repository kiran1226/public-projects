"""The triage queue: each proposal beside its issue, for the page and for the terminal.

A queue item joins what the committed log holds (numbers, labels, the answer) with
what only the local store holds (titles, text, the model's note). The matching
passage of each earlier issue is computed here, from the stored text.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .act import Plan, plan
from .models import Issue, Outcome, Prediction
from .store import Store
from .text import matching_passage, search_text

SHOWN_CANDIDATES = 3


@dataclass
class Earlier:
    issue: Issue
    passage: str
    proposed: bool = False


@dataclass
class QueueItem:
    issue: Issue
    prediction: Prediction
    note: str | None = None
    earlier: list[Earlier] = field(default_factory=list)
    outcome: Outcome | None = None

    @property
    def plan(self) -> Plan:
        """What the desk would do on its own in apply mode."""
        passages = {e.issue.number: e.passage for e in self.earlier}
        return plan(self.issue.number, self.prediction.proposal, passages)


def build_item(
    store: Store, prediction: Prediction, outcome: Outcome | None = None
) -> QueueItem | None:
    """Join one log row with the stored text. None if the issue is not in the store."""
    issue = store.issue(prediction.number)
    if issue is None:
        return None
    proposal = prediction.proposal
    numbers = list(prediction.candidates[:SHOWN_CANDIDATES])
    if proposal.duplicate_of is not None:
        numbers = [proposal.duplicate_of] + [n for n in numbers if n != proposal.duplicate_of]
        numbers = numbers[:SHOWN_CANDIDATES]
    query = search_text(issue.title, issue.body)
    earlier = []
    for number in numbers:
        other = store.issue(number)
        if other is not None:
            earlier.append(
                Earlier(
                    other,
                    matching_passage(query, other.body, other.title),
                    proposed=number == proposal.duplicate_of,
                )
            )
    return QueueItem(
        issue=issue,
        prediction=prediction,
        note=store.note(issue.number, prediction.system_version),
        earlier=earlier,
        outcome=outcome,
    )


def build_queue(
    store: Store,
    predictions: list[Prediction],
    outcomes: dict[int, Outcome] | None = None,
    config: str | None = None,
    limit: int | None = None,
) -> list[QueueItem]:
    """Newest issue first. With `config`, only that configuration's answers."""
    chosen = [p for p in predictions if config is None or p.config == config]
    chosen.sort(key=lambda p: (p.created_at, p.number), reverse=True)
    items = []
    for prediction in chosen:
        item = build_item(store, prediction, (outcomes or {}).get(prediction.number))
        if item is not None:
            items.append(item)
        if limit is not None and len(items) >= limit:
            break
    return items


def format_item(item: QueueItem) -> str:
    """One queue item as plain text, for the terminal."""
    proposal = item.prediction.proposal
    lines = [f"#{item.issue.number}  {item.issue.title}"]
    labels = ", ".join(f"{p.name} ({p.confidence})" for p in proposal.labels) or "none"
    lines.append(f"  labels:    {labels}")
    if proposal.duplicate_of is not None:
        lines.append(f"  duplicate: #{proposal.duplicate_of} ({proposal.duplicate_confidence})")
    else:
        lines.append("  duplicate: none proposed")
    for earlier in item.earlier:
        mark = "*" if earlier.proposed else " "
        lines.append(f"  {mark} #{earlier.issue.number} {earlier.issue.title[:70]}")
        lines.append(f"      “{earlier.passage}”")
    if proposal.gate:
        lines.append(f"  gate:      {', '.join(proposal.gate)}")
    route = "to a person: " + ", ".join(proposal.person) if proposal.person else "handled alone"
    lines.append(f"  route:     {route}  [{proposal.source}]")
    if item.note:
        lines.append(f"  note:      {item.note}")
    if item.outcome is not None:
        truth = ", ".join(item.outcome.labels) or "no label of the taxonomy"
        if item.outcome.duplicate:
            original = f"#{item.outcome.original}" if item.outcome.original else "unresolved"
            truth += f"; closed as a duplicate of {original}"
        lines.append(f"  maintainers: {truth}")
    return "\n".join(lines)
