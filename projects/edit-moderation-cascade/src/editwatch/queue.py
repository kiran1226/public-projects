"""The review queue: each edit with its risk, the marked lines and a sentence.

The model writes no sentence. The page words one from the category, so nothing an
edit says can end up quoted back to a reviewer as the system's own opinion.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Change, Edit, Prediction

SENTENCES = {
    "vandalism": "Looks like vandalism.",
    "spam_or_promotion": "Looks like promotion or spam.",
    "false_or_unsourced_claim": "Changes a fact without a source.",
    "test_or_accident": "Looks like a test or an accident.",
    "content_removal": "Removes content without a clear reason.",
    "other": "The model saw a problem it could not name.",
    "none": "The model saw no problem.",
}


@dataclass
class QueueItem:
    place: int
    edit: Edit
    prediction: Prediction
    change: Change | None
    reverted: bool | None = None

    @property
    def sentence(self) -> str:
        v = self.prediction.verdict
        if v.source != "model":
            why = {
                None: "Not in the slice the model reads.",
                "queue_full": "The model's queue was full.",
                "budget": "The day's budget was used up.",
                "no_model": "No model was available in this run.",
                "model_error": "The model gave no answer.",
                "gate": "The model's answer failed a check.",
                "window_closed": "The run ended before the model was asked.",
            }.get(v.skipped, "The model was not asked.")
            return f"Ordered by tier 1 alone. {why}"
        return SENTENCES.get(v.category or "none", SENTENCES["other"])

    @property
    def marked(self) -> list[int]:
        """Numbers of the change lines the model marked."""
        v = self.prediction.verdict
        if v.first_line is None or v.last_line is None:
            return []
        return list(range(v.first_line, v.last_line + 1))


def build_queue(
    rows: list[tuple[Edit, Prediction, Change | None]], reverted: dict[int, bool] | None = None
) -> list[QueueItem]:
    """Riskiest first. Ties go to the earlier revision."""
    ordered = sorted(rows, key=lambda r: (-r[1].verdict.risk, r[0].rev_id))
    return [
        QueueItem(place, edit, prediction, change, (reverted or {}).get(edit.rev_id))
        for place, (edit, prediction, change) in enumerate(ordered, start=1)
    ]


def format_item(item: QueueItem) -> str:
    """One queue entry as plain text, for the terminal."""
    v = item.prediction.verdict
    sign = "+" if item.edit.delta >= 0 else "-"
    who = "temporary account" if item.edit.temp else "registered account"
    lines = [
        f"{item.place:>3}. risk {v.risk:.2f}  {item.edit.title}  "
        f"({sign}{abs(item.edit.delta):,} bytes, {who})",
        f"     {item.sentence}"
        + (f" Model risk {v.model_risk}, tier 1 {v.tier1:.2f}." if v.source == "model" else ""),
    ]
    marked = set(item.marked)
    if item.change is not None:
        for number, line in enumerate(item.change.lines, start=1):
            flag = ">>" if number in marked else "  "
            lines.append(f"     {flag} {line[:150]}")
    if v.gate:
        lines.append(f"     gate: {', '.join(v.gate)}")
    if item.reverted is not None:
        lines.append(f"     outcome: {'reverted' if item.reverted else 'kept'}")
    return "\n".join(lines)
