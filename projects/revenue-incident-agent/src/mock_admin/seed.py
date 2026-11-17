"""What exists in a generated world, for seeding the service. Reads the world file directly."""

from __future__ import annotations

from pathlib import Path

import duckdb


def entities_from_world(world: Path) -> dict[str, set[str]]:
    """Coupons, releases, SKUs, providers and campaigns by kind, plus countries and channels."""
    con = duckdb.connect(str(world), read_only=True)
    try:
        found: dict[str, set[str]] = {}
        for kind, entity in con.execute("SELECT kind, entity FROM entities").fetchall():
            found.setdefault(str(kind), set()).add(str(entity))
        for place in ("country", "channel"):
            rows = con.execute(f"SELECT DISTINCT {place} FROM traffic").fetchall()
            found[place] = {str(row[0]) for row in rows}
        return found
    finally:
        con.close()
