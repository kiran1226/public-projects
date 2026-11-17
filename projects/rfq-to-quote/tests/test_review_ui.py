from pathlib import Path

import pytest

from rfq2quote.evals import load_gold, oracle_result
from rfq2quote.pipeline import build_pipeline
from rfq2quote.store import ReviewStore

pytest.importorskip("streamlit")
APP = str(Path(__file__).resolve().parents[1] / "app" / "review_ui.py")


def test_reviewer_can_approve_a_held_quote(catalog, data_dir, tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest

    db = tmp_path / "review.db"
    monkeypatch.setenv("RFQ2QUOTE_REVIEW_DB", str(db))
    pipeline = build_pipeline("baseline", catalog=catalog)
    gold = next(g for g in load_gold(data_dir) if g.id == "rfq-003")
    ReviewStore(db).save(oracle_result(pipeline, gold, data_dir))  # held: margin below floor

    app = AppTest.from_file(APP, default_timeout=30).run()
    assert not app.exception
    assert any("MARGIN_BELOW_FLOOR" in e.value for e in app.error)
    next(b for b in app.button if b.label == "Approve").click().run()
    assert not app.exception

    store = ReviewStore(db)
    assert store.counts() == {"approved": 1}
    assert store.events("rfq-003")[-1]["actor"] == "reviewer"
