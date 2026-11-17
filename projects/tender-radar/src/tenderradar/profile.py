"""What a supplier sells and where.

A panel supplier's profile is built from the awards it won before the cut date. The
scenario client's profile is written by hand in a YAML file, the way a bid desk would
describe itself.
"""

from __future__ import annotations

import gzip
import json
from collections import Counter
from collections.abc import Iterable
from pathlib import Path

import yaml

from . import cpv
from .models import PastWin, Profile

# A profile keeps the text of this many of its most recent wins.
MAX_TEXTS = 40
MAX_TEXT_CHARS = 600


def build_profile(profile_id: str, wins: Iterable[PastWin]) -> Profile:
    """A profile from past wins. Each win counts once, however many codes its notice lists."""
    ordered = sorted(wins, key=lambda w: (w.publication_date, w.procedure_id), reverse=True)
    countries = Counter(w.buyer_country for w in ordered if w.buyer_country)
    weights: Counter[str] = Counter()
    for win in ordered:
        codes = [c for c in dict.fromkeys(win.cpv) if cpv.valid(c)]
        for code in codes:
            weights[code] += 1.0 / len(codes)
    buyers = Counter(w.buyer for w in ordered if w.buyer)
    texts = list(dict.fromkeys(w.text[:MAX_TEXT_CHARS] for w in ordered if w.text.strip()))[
        :MAX_TEXTS
    ]
    return Profile(
        profile_id=profile_id,
        kind="supplier",
        countries=[country for country, _ in countries.most_common()],
        cpv_weights={code: round(weight, 4) for code, weight in weights.most_common()},
        texts=texts,
        buyers=dict(sorted(buyers.items())),
        past_wins=len(ordered),
    )


def load_client_profile(path: Path) -> Profile:
    """The scenario client's profile from YAML."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} does not hold a profile")
    categories = data.get("categories") or {}
    unknown = [code for code in categories if not cpv.valid(str(code))]
    if unknown:
        raise ValueError(f"{path}: not eight-digit category codes: {unknown}")
    return Profile(
        profile_id=str(data.get("id", "client")),
        kind="client",
        name=str(data.get("name", "")),
        description=str(data.get("description", "")).strip(),
        countries=list(data.get("countries") or []),
        cpv_weights={str(code): float(weight) for code, weight in categories.items()},
        texts=[str(t).strip() for t in (data.get("sells") or []) if str(t).strip()],
        min_value_eur=data.get("min_value_eur"),
        max_value_eur=data.get("max_value_eur"),
        min_days_to_deadline=int(data.get("min_days_to_deadline", 0)),
        excluded_procedure_types=list(data.get("excluded_procedure_types") or []),
        excluded_natures=list(data.get("excluded_natures") or []),
        capabilities=[str(c).strip() for c in (data.get("capabilities") or [])],
    )


def save_profiles(profiles: Iterable[Profile], path: Path) -> int:
    """Write profiles to a gzipped JSON-lines file. Returns how many were written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    # mtime=0 keeps the file byte-identical when the profiles are, so git shows real changes.
    with gzip.GzipFile(path, "wb", mtime=0) as raw:
        for profile in sorted(profiles, key=lambda p: p.profile_id):
            raw.write(
                (json.dumps(profile.model_dump(mode="json"), ensure_ascii=False) + "\n").encode()
            )
            count += 1
    return count


def load_profiles(path: Path) -> list[Profile]:
    if not path.exists():
        return []
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return [Profile.model_validate(json.loads(line)) for line in handle if line.strip()]
