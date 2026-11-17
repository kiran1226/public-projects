import shutil
import warnings
from pathlib import Path

import duckdb
import numpy as np
import pytest

from revwatch.models import Slice
from revwatch.scenarios import Label
from revwatch.slices import enumerate_slices
from revwatch.warehouse import METRICS, Panel, Warehouse
from tests.conftest import at

HOURS = 21 * 24


@pytest.fixture()
def wh(small_world: tuple[Path, list[Label]]) -> Warehouse:
    return Warehouse(small_world[0])


def test_missing_world_says_how_to_create_it(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="revwatch generate"):
        Warehouse(tmp_path / "nope.duckdb")


def test_the_world_is_read_on_one_thread(wh: Warehouse) -> None:
    # A sum of floats depends on the order of addition. With several threads that order
    # depends on how the file was written, which differs from one `generate` to the next,
    # and a recorded investigation then stops matching in the last digit of one figure.
    assert wh._con.execute("SELECT current_setting('threads')").fetchone() == (1,)


def test_meta_is_exposed(wh: Warehouse) -> None:
    assert wh.n_hours == HOURS
    assert wh.hour(25) == at(1, 1)
    assert wh.tick(at(1, 1)) == 25
    assert len(wh.world_hash) == 16


def test_panel_shapes(wh: Warehouse) -> None:
    assert wh.panel(METRICS["sessions"]).num.shape == (90, HOURS)
    assert wh.panel(METRICS["payment_success_rate"]).den.shape == (90, HOURS)
    assert wh.panel(METRICS["discount_rate"]).num.shape == (218, HOURS)


def test_overall_row_is_the_sum_of_the_countries(wh: Warehouse) -> None:
    panel = wh.panel(METRICS["sessions"])
    countries = [
        i for i, s in enumerate(panel.slices) if len(s.items) == 1 and s.items[0][0] == "country"
    ]
    assert len(countries) == 6
    fresh = ~np.isnan(panel.num[0])
    np.testing.assert_allclose(panel.num[0, fresh], panel.num[countries][:, fresh].sum(axis=0))


def test_rates_are_plausible(wh: Warehouse) -> None:
    conversion = wh.panel(METRICS["checkout_conversion"]).value[0]
    success = wh.panel(METRICS["payment_success_rate"]).value[0]
    margin = wh.panel(METRICS["gross_margin_rate"]).value[0]
    assert 0.03 < np.nanmean(conversion) < 0.07
    assert 0.90 < np.nanmean(success) < 0.96
    assert 0.35 < np.nanmean(margin) < 0.50


def test_hours_missing_from_a_table_are_nan_not_zero(wh: Warehouse) -> None:
    lo, hi = wh.tick(at(14, 3)), wh.tick(at(14, 5))
    sessions = wh.panel(METRICS["sessions"])
    conversion = wh.panel(METRICS["checkout_conversion"])
    payments = wh.panel(METRICS["payment_success_rate"])
    assert np.isnan(sessions.num[:, lo:hi]).all()
    assert np.isnan(conversion.value[:, lo:hi]).all()
    assert np.isfinite(payments.num[:, lo:hi]).all()
    assert np.isfinite(sessions.num[0, lo - 1]) and np.isfinite(sessions.num[0, hi])


def test_calendar_flags_follow_the_event_scope(wh: Warehouse) -> None:
    slices = enumerate_slices(("country", "platform", "channel"))
    flags = wh.calendar_flags(slices)
    promo, holiday = flags["planned_promotion"], flags["public_holiday"]
    lo, hi = wh.tick(at(16)), wh.tick(at(17))
    assert promo[:, lo:hi].all() and not promo[:, hi].any() and not promo[:, lo - 1].any()
    index = {s: i for i, s in enumerate(slices)}
    day = wh.tick(at(18, 12))
    assert holiday[index[Slice.of(country="ES")], day]
    assert holiday[index[Slice()], day]
    assert holiday[index[Slice.of(platform="app_ios")], day]
    assert not holiday[index[Slice.of(country="DE")], day]
    assert not flags["maintenance"].any()


def test_every_metric_states_its_definition_and_reason() -> None:
    for metric in METRICS.values():
        assert metric.definition and metric.why, metric.name
    assert [m.name for m in METRICS.values() if not m.detected] == ["net_revenue"]


def test_zero_volume_gives_nan_without_warnings() -> None:
    metric = METRICS["checkout_conversion"]
    panel = Panel(metric, (Slice(),), np.array([[0.0, 5.0]]), np.array([[0.0, 100.0]]))
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        value = panel.value
    assert np.isnan(value[0, 0]) and value[0, 1] == 0.05


def test_an_hour_with_rows_missing_for_part_of_a_table_is_nan_where_it_matters(
    small_world: tuple[Path, list[Label]], tmp_path: Path
) -> None:
    # One country's traffic arrives late for one hour; the rest of the table is there.
    path = tmp_path / "partial.duckdb"
    shutil.copy(small_world[0], path)
    with duckdb.connect(str(path)) as con:
        con.execute("DELETE FROM traffic WHERE country = 'DE' AND ts = ?", [at(4, 4)])
    with Warehouse(path) as partial:
        tick = partial.tick(at(4, 4))
        sessions = partial.panel(METRICS["sessions"])
        row = {s: i for i, s in enumerate(sessions.slices)}
        # Every slice that should contain German rows is unknown, not low.
        for where in (Slice(), Slice.of(country="DE"), Slice.of(platform="web_mobile")):
            assert np.isnan(sessions.num[row[where], tick]), where.key
        # Slices without German rows are complete.
        for where in (Slice.of(country="FR"), Slice.of(country="FR", platform="web_mobile")):
            assert np.isfinite(sessions.num[row[where], tick]), where.key
        assert np.isfinite(sessions.num[:, tick + 1]).all()
        conversion = partial.panel(METRICS["checkout_conversion"])
        assert np.isnan(conversion.value[row[Slice.of(country="DE")], tick])
        # The denominator of the total is unknown too: the sum of the rows that did
        # arrive would read as a jump in conversion.
        assert np.isnan(conversion.den[row[Slice()], tick])
        assert np.isfinite(conversion.den[row[Slice.of(country="FR")], tick])
        assert np.isfinite(partial.panel(METRICS["payment_success_rate"]).num[:, tick]).all()


def test_a_file_that_is_not_a_world_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "notes.duckdb"
    path.write_text("not a database")
    with pytest.raises(ValueError, match="not a revwatch world"):
        Warehouse(path)
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    with pytest.raises(ValueError, match="not a revwatch world"):
        Warehouse(empty)


def test_series_for_any_cut_agrees_with_the_panel(wh: Warehouse) -> None:
    metric = METRICS["payment_success_rate"]
    panel = wh.panel(metric)
    row = panel.slices.index(Slice.of(country="DE", payment_method="card"))
    num, den = wh.series(metric, {"country": "DE", "payment_method": "card"}, 100, 130)
    np.testing.assert_array_equal(num, panel.num[row, 100:130])
    np.testing.assert_array_equal(den, panel.den[row, 100:130])
    # A cut the detectors do not score: three dimensions, and the provider.
    narrow, _ = wh.series(
        metric, {"country": "DE", "payment_method": "card", "platform": "app_ios"}, 100, 130
    )
    assert 0 < narrow.sum() < num.sum()
    by_provider, _ = wh.series(metric, {"country": "DE", "provider": "psp_alpha"}, 100, 130)
    np.testing.assert_array_equal(by_provider, num)  # psp_alpha is card in Germany


def test_split_adds_up_to_the_whole(wh: Warehouse) -> None:
    metric = METRICS["checkout_conversion"]
    whole_num, whole_den = wh.series(metric, {"country": "FR"}, 200, 224)
    parts = wh.split(metric, {"country": "FR"}, "channel", 200, 224)
    assert set(parts) == {"direct", "organic", "paid_search", "email", "affiliate"}
    assert sum(num for num, _ in parts.values()) == pytest.approx(whole_num.sum())
    assert sum(den for _, den in parts.values()) == pytest.approx(whole_den.sum())
    providers = wh.split(METRICS["payment_success_rate"], {"country": "DE"}, "provider", 200, 224)
    assert set(providers) == {"psp_alpha", "psp_gamma", "psp_delta", "psp_epsilon"}


def test_a_cut_the_metric_does_not_have_is_refused(wh: Warehouse) -> None:
    with pytest.raises(ValueError, match="checkout_conversion has no dimension 'category'"):
        wh.series(METRICS["checkout_conversion"], {"category": "kitchen"}, 0, 5)
    with pytest.raises(ValueError, match="unknown country value"):
        wh.series(METRICS["sessions"], {"country": "DE'; DROP TABLE traffic; --"}, 0, 5)
    with pytest.raises(ValueError, match="cannot be split by 'provider'"):
        wh.split(METRICS["sessions"], {}, "provider", 0, 5)
    with pytest.raises(ValueError, match="cannot be split by 'country'"):
        wh.split(METRICS["sessions"], {"country": "DE"}, "country", 0, 5)


def test_changes_events_and_entities_are_readable(wh: Warehouse) -> None:
    around = wh.changes(at(10, 9), at(10, 11))
    blamed = [c for c in around if c["entity"] == "psp_alpha" and "elevated" in c["note"]]
    assert len(blamed) == 1 and blamed[0]["kind"] == "provider_status"
    assert wh.change(blamed[0]["change_id"]) == blamed[0]
    assert wh.change("CHG-99999") is None
    assert [e["kind"] for e in wh.events(at(16, 5), at(16, 6))] == ["planned_promotion"]
    assert wh.events(at(15, 0), at(15, 23)) == []
    assert "psp_alpha" in wh.entities()["provider"]
