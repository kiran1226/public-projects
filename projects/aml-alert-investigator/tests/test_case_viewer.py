from pathlib import Path

import pytest

from amlagent.baseline import investigate_baseline
from amlagent.store import CaseStore

pytest.importorskip("streamlit")
APP = str(Path(__file__).resolve().parents[1] / "app" / "case_viewer.py")


def test_empty_store_shows_a_hint(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("AML_AGENT_DB", str(tmp_path / "cases.db"))
    app = AppTest.from_file(APP, default_timeout=30).run()
    assert not app.exception and "aml-agent run" in app.info[0].value


def test_analyst_sees_real_values_and_can_approve(db, alert_flagged, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    path = tmp_path / "cases.db"
    monkeypatch.setenv("AML_AGENT_DB", str(path))
    CaseStore(path).save(*investigate_baseline(alert_flagged, db=db))

    app = AppTest.from_file(APP, default_timeout=30).run()
    assert not app.exception
    page = " ".join(m.value for m in app.markdown)
    assert "Anna Becker" in page and "CUST_01" not in page  # tokens restored for the analyst
    assert any("WATCHLIST_CANDIDATE" in w.value for w in app.warning)

    next(b for b in app.button if b.label == "Approve").click().run()
    assert not app.exception
    assert [d["decision"] for d in CaseStore(path).decisions("AL-900", "baseline")] == ["approved"]
