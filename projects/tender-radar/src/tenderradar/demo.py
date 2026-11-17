"""A morning list from the committed fixture: no feed, no key, no database.

The fixture is one real publication day of tenders. `tenderradar demo` runs the
scenario client's list on it through the same pipeline as the live run, with the
model's answers read from a recording.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from .filters import value_in_eur
from .models import Notice, Profile, Shortlist
from .notices import ParseError, parse_notice
from .pipeline import Assess, process_day
from .rank import Ranker

MODEL_RANKER = "fused+model"


def fixture_path(fixture_dir: Path) -> Path:
    found = sorted(fixture_dir.glob("notices-*.jsonl.gz"))
    if not found:
        raise FileNotFoundError(f"no fixture in {fixture_dir}; see scripts/build_fixture.py")
    return found[-1]


def load_fixture(path: Path) -> tuple[list[Notice], int]:
    """The fixture's notices, and how many records could not be read."""
    notices, failures = [], 0
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                notices.append(parse_notice(json.loads(line)))
            except ParseError:
                failures += 1
    return notices, failures


def client_list(
    notices: Sequence[Notice],
    client: Profile,
    rankers: Sequence[Ranker],
    *,
    assess: Assess | None,
    list_size: int,
    assess_top: int,
) -> Shortlist:
    """The client's list for the fixture day, with assessments when a recording has them."""
    day = max(n.publication_date for n in notices)
    fused = [r for r in rankers if r.name == "fused"]
    lists = process_day(
        day,
        notices,
        [client],
        fused,
        versions={"fused": "demo", MODEL_RANKER: "demo"},
        mode="backtest",
        now=datetime.now(UTC),
        list_size=list_size,
        assess=assess,
        assess_ranker="fused" if assess else None,
        assess_top=assess_top,
        assess_name=MODEL_RANKER,
    )
    return lists[-1]


def format_list(shortlist: Shortlist, notices: Sequence[Notice]) -> str:
    """The list as a bid manager would read it in a terminal."""
    by_number = {n.publication_number: n for n in notices}
    removed = ", ".join(f"{rule}: {count}" for rule, count in shortlist.removed.items()) or "none"
    lines = [
        f"Tenders first published {shortlist.day}: {len(notices)}",
        f"Removed by a rule: {removed}",
        f"Passed the rules: {shortlist.candidates}. Listed: {len(shortlist.entries)}.",
        "",
    ]
    for entry in shortlist.entries:
        notice = by_number[entry.publication_number]
        value = value_in_eur(notice)
        facts = [
            notice.buyer_country or "country not stated",
            f"deadline {notice.deadline}" if notice.deadline else "no deadline stated",
            f"about EUR {value:,.0f}" if value else "no value stated",
        ]
        verdict = "not read by the model"
        if entry.assessment:
            verdict = f"{entry.assessment.decision.replace('_', ' ')}, fit {entry.assessment.fit}"
        lines.append(f"{entry.rank:>2}. [{verdict}] {notice.title[:100]}")
        lines.append(f"    {' | '.join(facts)} | {notice.url}")
        if entry.assessment:
            lines.append(f"    {entry.assessment.reason}")
            for condition in entry.assessment.conditions:
                lines.append(f"    - {condition.requirement} (met: {condition.met})")
                lines.append(f'      "{condition.quote}"')
    return "\n".join(lines)
