from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pytest

from revwatch.scenarios import Effect, Label, Scenario, load_labels
from revwatch.world import WorldConfig, generate_world
from tests.conftest import at, small_scenarios


def query(path: Path, sql: str, params: list[object] | None = None) -> list[tuple[object, ...]]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


def meta(path: Path) -> dict[str, str]:
    return {str(k): str(v) for k, v in query(path, "SELECT key, value FROM meta")}


def test_tables_have_one_row_per_cell(small_world: tuple[Path, list[Label]]) -> None:
    path, _ = small_world
    hours = 21 * 24
    assert query(path, "SELECT COUNT(*) FROM payments") == [(hours * 6 * 4 * 5,)]
    assert query(path, "SELECT COUNT(*) FROM orders") == [(hours * 6 * 4 * 5 * 8,)]
    # Two hours of traffic were dropped by the late-data scenario.
    assert query(path, "SELECT COUNT(*) FROM traffic") == [((hours - 2) * 6 * 4 * 5,)]
    assert meta(path)["n_hours"] == str(hours)
    assert meta(path)["start"] == "2026-06-01T00:00:00"


def test_same_seed_gives_the_same_world(tmp_path: Path) -> None:
    config = WorldConfig(seed=11, days=14)
    generate_world(config, [], tmp_path / "a.duckdb")
    generate_world(config, [], tmp_path / "b.duckdb")
    generate_world(WorldConfig(seed=12, days=14), [], tmp_path / "c.duckdb")
    assert meta(tmp_path / "a.duckdb")["world_hash"] == meta(tmp_path / "b.duckdb")["world_hash"]
    assert meta(tmp_path / "a.duckdb")["world_hash"] != meta(tmp_path / "c.duckdb")["world_hash"]


def test_payment_failure_lowers_success_only_in_its_scope(
    small_world: tuple[Path, list[Label]],
) -> None:
    path, _ = small_world
    sql = (
        "SELECT SUM(successes) * 1.0 / SUM(attempts) FROM payments "
        "WHERE country = ? AND payment_method = 'card' AND ts >= ? AND ts < ?"
    )
    during = query(path, sql, ["DE", at(10, 10), at(10, 16)])[0][0]
    day_before = query(path, sql, ["DE", at(9, 10), at(9, 16)])[0][0]
    other_country = query(path, sql, ["FR", at(10, 10), at(10, 16)])[0][0]
    assert isinstance(during, float) and during < 0.45
    assert isinstance(day_before, float) and day_before > 0.85
    assert isinstance(other_country, float) and other_country > 0.85


def test_bot_traffic_raises_sessions_but_not_orders(small_world: tuple[Path, list[Label]]) -> None:
    path, _ = small_world
    sessions = "SELECT SUM(sessions) FROM traffic WHERE country = 'FR' AND channel = 'affiliate' AND ts >= ? AND ts < ?"
    orders = "SELECT SUM(orders) FROM orders WHERE country = 'FR' AND channel = 'affiliate' AND ts >= ? AND ts < ?"
    during, before = [at(9, 10), at(9, 16)], [at(8, 10), at(8, 16)]
    s_ratio = float(query(path, sessions, during)[0][0]) / float(
        query(path, sessions, before)[0][0]
    )  # type: ignore[arg-type]
    o_ratio = float(query(path, orders, during)[0][0]) / float(query(path, orders, before)[0][0])  # type: ignore[arg-type]
    assert s_ratio > 2.5
    assert 0.6 < o_ratio < 1.6


def test_late_data_removes_rows_from_one_table(small_world: tuple[Path, list[Label]]) -> None:
    path, _ = small_world
    window = [at(14, 3), at(14, 5)]
    assert query(path, "SELECT COUNT(*) FROM traffic WHERE ts >= ? AND ts < ?", window) == [(0,)]
    assert (
        query(path, "SELECT COUNT(*) FROM orders WHERE ts >= ? AND ts < ?", window)[0][0]
        == 2 * 6 * 4 * 5 * 8
    )


def test_labels_point_at_the_change_log(small_world: tuple[Path, list[Label]]) -> None:
    path, labels = small_world
    by_id = {label.scenario_id: label for label in labels}
    assert by_id["bot"].change_id is None
    change_id = by_id["pay"].change_id
    assert change_id is not None and change_id.startswith("CHG-")
    row = query(
        path,
        "SELECT ts, kind, entity, country, payment_method FROM changes WHERE change_id = ?",
        [change_id],
    )
    assert row == [
        (at(10, 10) - timedelta(minutes=15), "provider_status", "psp_alpha", "DE", "card")
    ]
    assert load_labels(path.with_suffix(".labels.json")) == labels


def test_change_ids_follow_time_order(small_world: tuple[Path, list[Label]]) -> None:
    path, _ = small_world
    rows = query(path, "SELECT change_id, ts FROM changes ORDER BY change_id")
    assert len(rows) > 21 * 10
    assert [r[1] for r in rows] == sorted(r[1] for r in rows)


def test_calendar_and_entities_are_written(small_world: tuple[Path, list[Label]]) -> None:
    path, _ = small_world
    events = query(path, "SELECT event_id, kind, country, start_ts FROM calendar ORDER BY start_ts")
    assert events == [
        ("CAL-001", "planned_promotion", None, at(16)),
        ("CAL-002", "public_holiday", "ES", at(18)),
    ]
    assert ("provider", "psp_alpha") in query(path, "SELECT kind, entity FROM entities")


def test_scenario_outside_the_world_names_the_scenario(tmp_path: Path) -> None:
    late = Scenario(
        id="too-late",
        kind="incident",
        cause="unknown",
        start=at(30),
        end=at(31),
        effects=[Effect(param="sessions_mult", value=0.5)],
    )
    with pytest.raises(ValueError, match="too-late: outside the world"):
        generate_world(WorldConfig(seed=1, days=14), [late], tmp_path / "w.duckdb")


def test_world_must_start_on_a_monday(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Monday"):
        generate_world(
            WorldConfig(seed=1, start=datetime(2026, 6, 2), days=14), [], tmp_path / "w.duckdb"
        )


def test_fixture_scenarios_are_valid() -> None:
    assert [s.id for s in small_scenarios()] == ["bot", "pay", "late", "promo", "holiday"]


def test_same_seed_and_scenarios_give_the_same_world(tmp_path: Path) -> None:
    config = WorldConfig(seed=11, days=21)
    a = generate_world(config, small_scenarios(), tmp_path / "a.duckdb")
    b = generate_world(config, small_scenarios(), tmp_path / "b.duckdb")
    assert meta(tmp_path / "a.duckdb")["world_hash"] == meta(tmp_path / "b.duckdb")["world_hash"]
    assert a == b
    assert query(tmp_path / "a.duckdb", "SELECT * FROM changes ORDER BY change_id") == query(
        tmp_path / "b.duckdb", "SELECT * FROM changes ORDER BY change_id"
    )
