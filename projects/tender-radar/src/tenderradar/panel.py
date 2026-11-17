"""The time split and the panel of real suppliers the backtest is scored on.

Everything a profile knows was published before the cut date. Everything it is tested
on was published after. A supplier is on the panel if it has enough history to build a
profile from and at least two wins in the test window to be scored on.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date

from .awards import SupplierIndex, supplier_hash
from .filters import apply_filters
from .models import Notice, Outcome, PastWin, Profile
from .profile import build_profile

MIN_PAST_WINS = 5
MIN_TEST_WINS = 2
PANEL_SIZE = 200


@dataclass
class Panel:
    profiles: list[Profile]
    # profile id -> supplier key. The key holds a company name or number, so this
    # mapping stays in memory and is never written to a committed file.
    keys: dict[str, str]
    eligible: int


def split_wins(
    wins: dict[str, PastWin], cut: date, test_procedures: dict[str, Notice]
) -> tuple[list[PastWin], list[PastWin]]:
    """A supplier's wins before the cut, and its wins of tenders in the test window."""
    past = [w for w in wins.values() if w.publication_date < cut]
    test = [
        w for w in wins.values() if w.publication_date >= cut and w.procedure_id in test_procedures
    ]
    return past, test


def select_panel(
    wins: dict[str, dict[str, PastWin]],
    index: SupplierIndex,
    cut: date,
    test_procedures: dict[str, Notice],
    *,
    size: int = PANEL_SIZE,
    min_past: int = MIN_PAST_WINS,
    min_test: int = MIN_TEST_WINS,
) -> Panel:
    """Choose the panel and build each supplier's profile from its wins before the cut."""
    by_country: dict[str, list[tuple[str, str, list[PastWin]]]] = defaultdict(list)
    eligible = 0
    for key, supplier_wins in wins.items():
        if not index.is_company(key):
            continue
        past, test = split_wins(supplier_wins, cut, test_procedures)
        if len(past) < min_past or len(test) < min_test:
            continue
        eligible += 1
        home = Counter(w.buyer_country or "?" for w in past).most_common(1)[0][0]
        by_country[home].append((supplier_hash(key), key, past))
    # Take suppliers country by country in turn, so one large country cannot fill the
    # panel. Within a country the order is by hashed id: arbitrary, but the same every run.
    for members in by_country.values():
        members.sort()
    chosen: list[tuple[str, str, list[PastWin]]] = []
    depth = 0
    while len(chosen) < size and any(depth < len(m) for m in by_country.values()):
        for country in sorted(by_country):
            members = by_country[country]
            if depth < len(members) and len(chosen) < size:
                chosen.append(members[depth])
        depth += 1
    chosen.sort()
    return Panel(
        profiles=[build_profile(profile_id, past) for profile_id, _key, past in chosen],
        keys={profile_id: key for profile_id, key, _past in chosen},
        eligible=eligible,
    )


def panel_outcomes(
    panel: Panel,
    wins: dict[str, dict[str, PastWin]],
    cut: date,
    test_procedures: dict[str, Notice],
) -> list[Outcome]:
    """What each panel supplier went on to win in the test window."""
    by_id = {profile.profile_id: profile for profile in panel.profiles}
    outcomes = []
    for profile_id, key in panel.keys.items():
        _past, test = split_wins(wins[key], cut, test_procedures)
        for win in test:
            notice = test_procedures[win.procedure_id]
            hits = apply_filters(by_id[profile_id], notice, notice.publication_date)
            outcomes.append(
                Outcome(
                    profile_id=profile_id,
                    procedure_id=win.procedure_id,
                    arrived=win.publication_date,
                    publication_number=notice.publication_number,
                    day=notice.publication_date,
                    filter_hits=[hit.rule for hit in hits],
                )
            )
    return sorted(outcomes, key=lambda o: (o.day, o.profile_id, o.procedure_id))
