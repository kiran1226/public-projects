from datetime import date

from amlagent.bankdb import BankDB, load_alerts, load_gold
from amlagent.features import activity_summary, hard_flags, screen
from amlagent.policy import Policy

P = Policy()


def test_activity_summary_is_exact(db):
    s = activity_summary(db, "A0001", date(2026, 6, 1), date(2026, 6, 3), 3000, P)
    assert (s["total_in"], s["total_out"], s["cash_in_share"]) == (28600.0, 20000.0, 1.0)
    assert s["structuring"] == {"count": 3, "total": 28600.0, "band": [8000.0, 10000.0]}
    assert s["pass_through_ratio"] == 0.67  # T000002 and T000003 left within 48 hours
    assert s["turnover_ratio"] == 9.53
    assert s["distinct_senders"] == 0
    assert s["by_month"] == {"2026-06": {"in": 28600.0, "out": 20000.0, "count": 4}}
    assert s["top_counterparties"] == [
        {"counterparty_id": "P0001", "in": 0.0, "out": 20000.0, "count": 1}
    ]


def test_empty_window_and_zero_turnover_do_not_divide_by_zero(db):
    s = activity_summary(db, "A0003", date(2026, 1, 1), date(2026, 1, 31), 0, P)
    assert (s["total_in"], s["cash_in_share"], s["pass_through_ratio"]) == (0.0, 0.0, 0.0)
    assert s["turnover_ratio"] is None and s["top_counterparties"] == []


def test_screen_matches_aliases_and_ignores_accents_and_case(db):
    hit = {
        "entry_id": "W001",
        "list": "sanctions",
        "kind": "person",
        "name_similarity": 1.0,
        "dob_match": "match",
        "country_match": "match",
    }
    assert screen(db, "Jonas Wolff", "1985-02-02", "DE", 0.85) == [hit]
    assert screen(db, "WOLFF,  Jónas", "", "FR", 0.85) == [
        hit | {"dob_match": "unknown", "country_match": "mismatch"}
    ]
    assert screen(db, "Lea Hartmann", "1984-11-02", "DE", 0.85) == []


def test_hard_flags(db, alert_flagged, alert_clean):
    assert hard_flags(db, alert_flagged, P) == ["WATCHLIST_CANDIDATE", "PRIOR_ESCALATION"]
    assert hard_flags(db, alert_clean, P) == []
    big = alert_clean.model_copy(update={"amount": 100000.0})
    assert hard_flags(db, big, P) == ["LARGE_AMOUNT"]
    risky = alert_clean.model_copy(
        update={"customer_id": "C0002", "account_id": "A0002", "counterparty_ids": []}
    )
    assert hard_flags(db, risky, P) == ["HIGH_RISK_RATING"]


def test_prior_escalation_expires(db, alert_flagged):
    late = alert_flagged.model_copy(update={"as_of": date(2027, 6, 30), "counterparty_ids": []})
    assert hard_flags(db, late, P) == []


def test_generated_dataset_flags_match_its_labels():
    db, gold = BankDB(), load_gold()
    for alert in load_alerts():
        flags, g = hard_flags(db, alert, P), gold[alert.alert_id]
        if g.difficulty == "clear":
            assert flags == [], (alert.alert_id, g.scenario, flags)
        if g.scenario in ("sanctions_match", "false_match"):
            assert "WATCHLIST_CANDIDATE" in flags, alert.alert_id
        if g.scenario == "high_risk":
            assert "HIGH_RISK_RATING" in flags
        if g.scenario == "property_sale":
            assert flags == ["LARGE_AMOUNT"]
        if g.scenario == "profile_mismatch":
            assert "PRIOR_ESCALATION" in flags
