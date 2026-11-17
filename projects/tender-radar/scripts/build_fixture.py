"""Cut the committed fixture from the local database: one real publication day of tenders.

The fixture lets tests and `tenderradar demo` run on real notices without the feed.
It holds competition notices only, as the feed returned them. Those name buyers, which
are public bodies, and no suppliers.

    uv run python scripts/build_fixture.py 2026-05-12
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from datetime import date

from tenderradar.config import load_settings
from tenderradar.store import COMPETITION, Store

NOTICE = """Tender notices in this folder
=============================

Source: TED (Tenders Electronic Daily), the online version of the Supplement to the
Official Journal of the EU, https://ted.europa.eu, fetched through its search API.

Content: the competition notices in category divisions {divisions} whose procedure was
first published on {day}, {count} notices, as the API returned them, with two changes.
The `links` field (document URLs) was dropped. Email addresses that buyers typed into
free text were replaced by "[email removed]": {emails} occurrences. Nothing else was
edited. Contact fields were never requested.

Reuse: the TED legal notice states that procurement notices "can be freely reused, for
commercial or non-commercial purposes", under the Commission Decision of 12 December
2011 on the reuse of Commission documents (read on 2026-11-17 at
https://ted.europa.eu/en/legal-notice). (c) European Union.
"""


EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
REMOVED = "[email removed]"


def scrub(value):
    """Drop email addresses from every string in a record. Returns (value, how many)."""
    if isinstance(value, str):
        return EMAIL.subn(REMOVED, value)
    if isinstance(value, list):
        pairs = [scrub(item) for item in value]
        return [item for item, _ in pairs], sum(count for _, count in pairs)
    if isinstance(value, dict):
        pairs = {key: scrub(item) for key, item in value.items() if key != "links"}
        return {key: item for key, (item, _) in pairs.items()}, sum(c for _, c in pairs.values())
    return value, 0


def main(day_text: str) -> None:
    day = date.fromisoformat(day_text)
    settings = load_settings()
    store = Store(settings.db_path)
    new = {n.publication_number for n in store.new_procedures(day, day)}
    records = [
        r for r in store.raw_records(COMPETITION, day, day) if r.get("publication-number") in new
    ]
    scrubbed = [scrub(record) for record in records]
    records = [record for record, _ in scrubbed]
    emails = sum(count for _, count in scrubbed)
    out = settings.fixture_dir
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"notices-{day.isoformat()}.jsonl.gz"
    # mtime=0 keeps the file byte-identical when the records are.
    with gzip.GzipFile(path, "wb", mtime=0) as raw:
        for record in sorted(records, key=lambda r: r["publication-number"]):
            raw.write((json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n").encode())
    (out / "NOTICE").write_text(
        NOTICE.format(
            divisions=", ".join(settings.divisions),
            day=day.isoformat(),
            count=len(records),
            emails=emails,
        ),
        encoding="utf-8",
    )
    print(f"{len(records)} notices written to {path} ({path.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2026-05-12")
