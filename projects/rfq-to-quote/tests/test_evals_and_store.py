import pytest

from rfq2quote.evals import (
    ORIGINAL_SPLITS,
    evaluate_config,
    f1,
    load_gold,
    oracle_result,
    overlap,
    score_fields,
)
from rfq2quote.models import RFQ
from rfq2quote.pipeline import build_pipeline
from rfq2quote.store import ReviewStore, save_correction

MUST_REVIEW = {
    "rfq-003", "rfq-004", "rfq-006", "rfq-009", "rfq-011", "rfq-012", "rfq-014", "rfq-015",
    "rfq-018", "rfq-019", "rfq-020", "rfq-021", "rfq-026", "rfq-028", "rfq-029",
    "rfq-031", "rfq-034", "rfq-037", "rfq-039",
}  # fmt: skip
# The harder set: six products we do not sell, six requests ambiguous on purpose, and
# one (hard-015) where the volume discount takes a line under the margin floor.
HARD_MUST_REVIEW = {
    "hard-002", "hard-008", "hard-009", "hard-011", "hard-015", "hard-016", "hard-018",
    "hard-020", "hard-026", "hard-027", "hard-029", "hard-034", "hard-036",
}  # fmt: skip


@pytest.fixture(scope="module")
def gold(data_dir):
    return load_gold(data_dir)


@pytest.fixture(scope="module")
def baseline(catalog):
    return build_pipeline("baseline", catalog=catalog)


def test_dataset_is_consistent(catalog, data_dir, gold):
    assert [g.split for g in gold].count("dev") == 30
    assert [g.split for g in gold].count("holdout") == 10
    assert [g.split for g in gold].count("hard") == 32
    for case in gold:
        assert (data_dir / case.source).exists(), case.source
        for item in case.items:
            assert item.sku is None or catalog.get(item.sku), f"{case.id}: {item.sku}"
            assert item.unit in (None, "m", "pcs")


def test_the_original_sets_come_first_and_a_set_can_be_loaded_on_its_own(data_dir, gold):
    assert [g.split for g in gold] == ["dev"] * 30 + ["holdout"] * 10 + ["hard"] * 32
    assert [g.id for g in gold[:40]] == [f"rfq-{n:03d}" for n in range(1, 41)]
    assert [g.id for g in load_gold(data_dir, ORIGINAL_SPLITS)] == [g.id for g in gold[:40]]
    assert {g.split for g in load_gold(data_dir, ("hard",))} == {"hard"}


def test_overlap_and_f1():
    assert overlap([(1, "m"), (1, "m"), (2, "pcs")], [(1, "m"), (3, "pcs")]) == 1
    assert f1(0, 0, 0) == 1.0 and f1(0, 2, 2) == 0.0 and f1(1, 2, 2) == 0.5


def test_field_scoring_accepts_city_aliases_and_legal_form_differences(gold):
    case = next(g for g in gold if g.id == "rfq-010")
    rfq = RFQ(customer_name="Eventtechnik Sommer", customer_email="LAGER@eventtechnik-sommer.example",
              delivery_date="2026-10-07", delivery_location="Cologne", line_items=[],
              missing_info=[])  # fmt: skip
    assert all(score_fields(rfq, case).values())


def test_expected_routes_follow_policy(baseline, gold, data_dir):
    routes = {g.id: oracle_result(baseline, g, data_dir) for g in gold}
    held = {k for k, r in routes.items() if r.route == "human_review"}
    assert held == MUST_REVIEW | HARD_MUST_REVIEW


def test_the_size_rule_holds_no_request_of_any_set_when_given_its_label(baseline, gold, data_dir):
    # Expected routes are derived by the rules. A rule added later must not move them.
    fired = [
        g.id
        for g in gold
        if any(f.code == "SIZE_MISMATCH" for f in oracle_result(baseline, g, data_dir).flags)
    ]
    assert fired == []


def test_every_money_and_date_rule_has_a_case_where_it_is_the_only_reason(baseline, gold, data_dir):
    # Otherwise a misread date or total could never show up as an unsafe approval.
    alone = set()
    for g in gold:
        fired = {
            f.code for f in oracle_result(baseline, g, data_dir).flags if f.severity == "review"
        }
        if len(fired) == 1:
            alone |= fired
    assert {"LEAD_TIME_RISK", "HIGH_ORDER_VALUE", "MARGIN_BELOW_FLOOR", "BELOW_MOQ",
            "UNMATCHED_ITEM", "OPEN_QUESTION"} <= alone  # fmt: skip


def test_baseline_eval_reports_every_split(catalog, data_dir, gold):
    report = evaluate_config("baseline", gold, data_dir, catalog=catalog)
    dev, holdout = report.metrics("dev"), report.metrics("holdout")
    assert report.skipped is None and (dev["cases"], holdout["cases"]) == (30, 10)
    assert report.metrics("hard")["cases"] == 32
    assert 0.5 < dev["line_f1"] < 0.9  # a real baseline: useful, far from solved
    assert dev["auto_approve_ceiling"] == 0.5
    assert dev["cost_per_rfq_usd"] == 0


def test_wrong_recipient_or_date_on_an_approved_quote_counts_as_unsafe(catalog, data_dir, gold):
    report = evaluate_config("baseline", gold, data_dir, catalog=catalog)
    unsafe = {c.id for c in report.cases if c.unsafe_auto_approval}
    assert "rfq-005" in unsafe  # three drums of 500 m quoted as 500 m
    assert "rfq-017" in unsafe  # every line right, but "end of October" was not read


def test_llm_config_without_recordings_is_reported_as_not_run(catalog, data_dir, gold, tmp_path):
    (tmp_path / "rfqs").symlink_to(data_dir / "rfqs")
    report = evaluate_config("llm-v2", gold[:1], tmp_path, catalog=catalog)
    assert report.skipped and "--live" in report.skipped


def held_result(baseline, gold, data_dir, rfq_id="rfq-011"):
    return oracle_result(baseline, next(g for g in gold if g.id == rfq_id), data_dir)


def test_review_store_keeps_decisions_and_logs_every_action(baseline, gold, data_dir, tmp_path):
    result = held_result(baseline, gold, data_dir)  # held: drums without a length
    store = ReviewStore(tmp_path / "review.db")
    assert store.save(result) == "pending"
    store.decide("rfq-011", "rejected", "dana", "customer withdrew")

    assert store.save(result) == "rejected"  # re-processing does not reopen it
    assert store.counts() == {"rejected": 1}
    assert [(e["action"], e["actor"]) for e in store.events("rfq-011")] == [
        ("pending", "agent"), ("rejected", "dana"), ("reprocess_skipped", "agent"),
    ]  # fmt: skip
    with pytest.raises(KeyError):
        store.decide("no-such-id", "approved", "dana")


def test_stats_report_holds_and_overrides(baseline, gold, data_dir, tmp_path):
    store = ReviewStore(tmp_path / "review.db")
    for rfq_id in ("rfq-001", "rfq-011", "rfq-014"):
        store.save(held_result(baseline, gold, data_dir, rfq_id))
    store.decide("rfq-011", "corrected", "dana")
    store.decide("rfq-014", "approved", "dana")
    stats = store.stats()
    assert stats["requests"] == 3 and stats["auto_approved_share"] == pytest.approx(1 / 3)
    assert stats["override_rate"] == 0.5
    assert stats["hold_reasons"]["BELOW_MOQ"] == 2


def test_correction_never_replaces_a_hand_label(baseline, gold, data_dir, tmp_path):
    result = held_result(baseline, gold, data_dir)
    path = save_correction(result, tmp_path)
    assert path.parent.name == "corrections"
    assert [g.split for g in load_gold(tmp_path)] == ["correction"]  # a new case on its own

    (tmp_path / "gold").mkdir()
    hand_label = data_dir / "gold" / "rfq-011.json"
    (tmp_path / "gold" / "rfq-011.json").write_text(hand_label.read_text(encoding="utf-8"))
    assert [g.split for g in load_gold(tmp_path)] == ["dev"]  # the hand label wins


def test_progress_is_logged_every_batch_and_at_the_end(catalog, data_dir, gold):
    lines = []
    evaluate_config("baseline", gold[:40], data_dir, workers=8, log=lines.append, catalog=catalog)
    assert lines[0].startswith("[baseline: requests] 8/40 done")
    assert [line.split("] ")[1].split(" ")[0] for line in lines] == [
        "8/40", "16/40", "24/40", "32/40", "40/40",
    ]  # fmt: skip


def test_a_different_document_with_the_same_name_gets_its_own_entry(
    baseline, gold, data_dir, tmp_path
):
    first = held_result(baseline, gold, data_dir)
    first = first.model_copy(
        update={"provenance": first.provenance.model_copy(update={"doc_sha256": "a" * 64})}
    )
    other = first.model_copy(
        update={"provenance": first.provenance.model_copy(update={"doc_sha256": "b" * 64})}
    )
    store = ReviewStore(tmp_path / "review.db")
    store.save(first)
    store.decide("rfq-011", "rejected", "dana")

    assert store.save(other) == "pending"  # not hidden behind the earlier decision
    assert store.counts() == {"rejected": 1, "pending": 1}
    ids = [rfq_id for rfq_id, _, _ in store.list()]
    assert ids == ["rfq-011", "rfq-011-bbbbbbbb"]
    assert store.list("pending")[0][2].rfq_id == "rfq-011-bbbbbbbb"
    assert store.save(other) == "pending" and len(store.list()) == 2  # same file again
    assert "name_collision" in [e["action"] for e in store.events("rfq-011")]
