import filecmp

from amlagent.bankdb import DATA_DIR, BankDB, load_alerts, load_gold, load_planted


def test_shape_of_the_committed_dataset():
    alerts, gold = load_alerts(), load_gold()
    assert len(alerts) == 40
    assert sum(a.split == "heldout" for a in alerts) == 12
    assert {a.alert_id for a in alerts} == set(gold)
    assert sum(g.outcome == "escalate" for g in gold.values()) == 9


def test_gold_evidence_exists_in_the_bank():
    db = BankDB()
    watch = {w["entry_id"] for w in db.watchlist()}
    for alert in load_alerts():
        priors = {p["prior_id"] for p in db.prior_alerts(alert.customer_id, before=alert.as_of)}
        for ref in load_gold()[alert.alert_id].key_evidence:
            assert db.transaction(ref) or ref in watch or ref in priors, (alert.alert_id, ref)
        assert all(db.transaction(t) for t in alert.txn_ids)


def test_planted_identifiers_appear_in_free_text():
    texts = " ".join(BankDB().texts())
    planted = load_planted()
    assert planted and all(p in texts for p in planted)
    assert not set(planted) & BankDB().identifier_inventory()


def test_generator_is_deterministic(tmp_path):
    import build_dataset

    build_dataset.main(tmp_path)
    for sub in ("core", "alerts", "gold"):
        cmp = filecmp.dircmp(tmp_path / sub, DATA_DIR / sub)
        assert not cmp.diff_files and not cmp.left_only and not cmp.right_only, sub
