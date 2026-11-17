from amlagent.models import CaseResult, Disposition, Finding
from amlagent.privacy import Vault
from amlagent.store import CaseStore


def sample(alert_id="AL-001", config="routed", queue="analyst_review"):
    vault = Vault(alert_id)
    token = vault.token("TXN", "T000001")
    disposition = Disposition(
        outcome="close_false_positive",
        reason_code="explained_one_off",
        confidence="high",
        findings=[Finding(statement="Bonus", evidence_ids=[token])],
    )
    return CaseResult(
        alert_id=alert_id, config=config, tier="triage", disposition=disposition, queue=queue
    ), vault


def test_save_get_and_list(tmp_path):
    store = CaseStore(tmp_path / "cases.db")
    store.save(*sample())
    store.save(*sample("AL-002", queue="senior_review"))
    store.save(*sample("AL-001", config="haiku"))
    result, vault = store.get("AL-001", "routed")
    assert (
        result.disposition.reason_code == "explained_one_off"
        and vault.resolve("TXN_0001") == "T000001"
    )
    assert [r.alert_id for r in store.list("routed")] == ["AL-001", "AL-002"]
    assert len(store.list()) == 3 and store.get("AL-404", "routed") is None


def test_saving_again_replaces_the_case(tmp_path):
    store = CaseStore(tmp_path / "cases.db")
    store.save(*sample())
    store.save(*sample(queue="manual_investigation"))
    assert [r.queue for r in store.list("routed")] == ["manual_investigation"]


def test_analyst_decisions_are_kept_in_order(tmp_path):
    store = CaseStore(tmp_path / "cases.db")
    store.save(*sample())
    store.decide("AL-001", "routed", "approved")
    store.decide("AL-001", "routed", "overridden", note="Customer file shows a second employer.")
    decisions = store.decisions("AL-001", "routed")
    assert [d["decision"] for d in decisions] == ["approved", "overridden"]
    assert decisions[1]["note"].startswith("Customer file") and decisions[0]["analyst"] == "analyst"


def test_path_comes_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AML_AGENT_DB", str(tmp_path / "env.db"))
    CaseStore().save(*sample())
    assert (tmp_path / "env.db").exists()
