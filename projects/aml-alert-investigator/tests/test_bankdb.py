from datetime import date


def test_lookups(db):
    assert db.customer("C0001")["name"] == "Anna Becker"
    assert db.customer("C9999") is None
    assert [a["account_id"] for a in db.accounts("C0001")] == ["A0001"]
    assert db.counterparty("P0003")["country"] == "VD"


def test_transactions_are_filtered_and_ordered(db):
    rows = db.transactions("A0001", date(2026, 6, 1), date(2026, 6, 3))
    assert [r["txn_id"] for r in rows] == ["T000001", "T000002", "T000003", "T000004"]
    cash = db.transactions(
        "A0001",
        date(2026, 6, 1),
        date(2026, 6, 30),
        direction="in",
        channel="cash",
        min_amount=9300,
    )
    assert [r["txn_id"] for r in cash] == ["T000001", "T000003"]
    assert isinstance(cash[0]["amount"], float)


def test_prior_alerts_exclude_the_future(db):
    assert [p["prior_id"] for p in db.prior_alerts("C0001", before=date(2026, 6, 30))] == ["H001"]
    assert db.prior_alerts("C0001", before=date(2026, 1, 1)) == []


def test_links_work_from_either_side(db):
    assert db.links("C0002") == [{"customer_id": "C0001", "link_type": "shared_director"}]
    assert db.links("C0001") == [{"customer_id": "C0002", "link_type": "shared_director"}]


def test_identifier_inventory_holds_real_identifiers_only(db):
    inventory = db.identifier_inventory()
    assert {
        "Anna Becker",
        "DE44HARB1234567890",
        "anna.becker@example.com",
        "Lindenweg 12",
        "Jonas Wolff",
    } <= inventory
    assert (
        "" not in inventory and "Dmitar Kovalenko" not in inventory
    )  # the watchlist is public data
