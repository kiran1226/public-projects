import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

from mock_admin.app import create_app
from revwatch.actions import ROUTES, Executor, Refused, approve
from revwatch.findings import ALLOWED_MITIGATION
from revwatch.scenarios import TARGET_KEYS, Mitigation
from revwatch.store import PendingAction, Store
from tests.test_mock_admin import ENTITIES
from tests.test_store import COUPON, put

KEY = "INC-1:disable_coupon:code=SOMMER15"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "queue.sqlite")
    put(s)
    return s


@pytest.fixture()
def admin(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "admin.sqlite", ENTITIES), base_url="http://admin.test")


def executor(store: Store, client: httpx.Client) -> Executor:
    return Executor(store, "http://admin.test", client)


def calls(store: Store) -> list[dict]:  # type: ignore[type-arg]
    return [e["data"] for e in store.events("INC-1") if e["kind"] == "admin_call"]


def test_an_approved_mitigation_is_applied_and_every_call_is_in_the_audit_log(
    store: Store, admin: TestClient
) -> None:
    assert approve(store, executor(store, admin), "INC-1", "j.meier", "looks right") == "mitigated"
    applied = admin.get("/actions").json()
    assert [(a["action"], a["target"], a["incident_id"], a["reviewer"]) for a in applied] == [
        ("disable_coupon", {"code": "SOMMER15"}, "INC-1", "j.meier")
    ]
    (call,) = calls(store)
    assert call["method"] == "POST" and call["path"] == "http://admin.test/coupons/SOMMER15/disable"
    assert call["body"] == {"incident_id": "INC-1", "reviewer": "j.meier"}
    assert (call["idempotency_key"], call["attempt"], call["status_code"]) == (KEY, 1, 200)
    assert call["response"]["changed"] is True and call["latency_ms"] >= 0
    assert store.action_for("INC-1").detail == "applied"  # type: ignore[union-attr]


def test_approving_twice_makes_one_call(store: Store, admin: TestClient) -> None:
    run = executor(store, admin)
    assert approve(store, run, "INC-1", "j.meier") == "mitigated"
    assert approve(store, run, "INC-1", "j.meier") == "mitigated"
    assert len(calls(store)) == 1 and len(admin.get("/actions").json()) == 1
    assert "approve_ignored" in [e["kind"] for e in store.events("INC-1")]


def test_the_body_carries_what_the_path_does_not(tmp_path: Path, admin: TestClient) -> None:
    s = Store(tmp_path / "q2.sqlite")
    failover = Mitigation(
        action="failover_payment_provider", target={"provider": "psp_alpha", "country": "DE"}
    )
    put(s, proposal=failover)
    assert approve(s, executor(s, admin), "INC-1", "j.meier") == "mitigated"
    assert admin.get("/actions").json()[0]["target"] == {"provider": "psp_alpha", "country": "DE"}


def failing(responses: list[httpx.Response | Exception], seen: list[httpx.Request]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_timeout_is_retried_once_with_the_same_key_then_reported(store: Store) -> None:
    seen: list[httpx.Request] = []
    client = failing([httpx.ReadTimeout("slow"), httpx.ConnectError("refused")], seen)
    assert approve(store, executor(store, client), "INC-1", "j.meier") == "action_failed"
    assert [r.headers["Idempotency-Key"] for r in seen] == [KEY, KEY]
    assert [c["attempt"] for c in calls(store)] == [1, 2]
    assert [c["error"] for c in calls(store)] == [
        "admin service not reached: ReadTimeout",
        "admin service not reached: ConnectError",
    ]
    action = store.action_for("INC-1")
    assert action is not None and action.status == "failed" and action.attempts == 2
    assert "ConnectError" in action.detail
    # The approval is kept; the incident waits in the queue with the reason.
    assert [d["decision"] for d in store.decisions("INC-1")] == ["approve"]


def test_a_server_error_then_success_is_one_action(store: Store) -> None:
    seen: list[httpx.Request] = []
    ok = httpx.Response(200, json={"changed": True, "state": "disabled"})
    client = failing([httpx.Response(503, json={"detail": "overloaded"}), ok], seen)
    assert approve(store, executor(store, client), "INC-1", "j.meier") == "mitigated"
    assert [c["status_code"] for c in calls(store)] == [503, 200] and len(seen) == 2


def test_a_client_error_is_not_retried(store: Store) -> None:
    seen: list[httpx.Request] = []
    client = failing([httpx.Response(404, json={"detail": "unknown coupon: SOMMER15"})], seen)
    assert approve(store, executor(store, client), "INC-1", "j.meier") == "action_failed"
    assert len(seen) == 1
    assert (
        store.action_for("INC-1").detail == "admin service returned 404: unknown coupon: SOMMER15"
    )  # type: ignore[union-attr]


def test_a_failed_action_can_be_retried_and_reuses_its_key(store: Store, admin: TestClient) -> None:
    down = failing([httpx.ConnectError("refused")] * 2, [])
    assert approve(store, executor(store, down), "INC-1", "j.meier") == "action_failed"
    assert approve(store, executor(store, admin), "INC-1", "j.meier") == "mitigated"
    assert [c["attempt"] for c in calls(store)] == [1, 2, 3]
    assert {c["idempotency_key"] for c in calls(store)} == {KEY}
    assert len(store.decisions("INC-1")) == 1 and len(admin.get("/actions").json()) == 1


def test_a_crash_between_the_call_and_the_record_does_not_act_twice(
    store: Store, admin: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    run = executor(store, admin)
    finish = store.finish_action

    def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("process killed")

    monkeypatch.setattr(store, "finish_action", crash)
    with pytest.raises(RuntimeError):
        approve(store, run, "INC-1", "j.meier")
    # The service acted, the store never heard: the action is still pending.
    assert len(admin.get("/actions").json()) == 1
    assert store.get("INC-1").status == "action_pending"
    monkeypatch.setattr(store, "finish_action", finish)
    assert approve(store, run, "INC-1", "j.meier") == "mitigated"
    assert len(admin.get("/actions").json()) == 1  # the same key: the first answer, no second act
    assert [c["response"]["changed"] for c in calls(store)] == [True, True]


def test_the_executor_refuses_what_did_not_pass_the_guardrails(
    store: Store, admin: TestClient
) -> None:
    run = executor(store, admin)
    other = PendingAction("k", "INC-1", "disable_coupon", {"code": "VIP20"}, "j.meier")
    with pytest.raises(Refused, match="no approved proposal"):
        run.run(other)
    with pytest.raises(Refused, match="not in the mitigation catalogue"):
        run.run(PendingAction("k", "INC-1", "delete_database", {"name": "orders"}, "j.meier"))
    with pytest.raises(Refused, match="needs target code"):
        run.run(PendingAction("k", "INC-1", "disable_coupon", {"sku": "LIG-40417"}, "j.meier"))
    assert admin.get("/actions").json() == [] and calls(store) == []


def test_the_executor_refuses_a_proposal_nobody_approved(store: Store, admin: TestClient) -> None:
    # The action matches the open incident's proposal exactly, but no reviewer approved it.
    forged = PendingAction(KEY, "INC-1", "disable_coupon", {"code": "SOMMER15"}, "nobody")
    with pytest.raises(Refused, match="has not been approved"):
        executor(store, admin).run(forged)
    assert admin.get("/actions").json() == [] and calls(store) == []
    assert store.get("INC-1").status == "open" and store.decisions("INC-1") == []


def test_the_executor_sends_only_the_action_the_approval_stored(
    store: Store, admin: TestClient
) -> None:
    stored = store.approve("INC-1", "j.meier")
    assert stored is not None
    forged = PendingAction("another-key", "INC-1", "disable_coupon", {"code": "SOMMER15"}, "x")
    with pytest.raises(Refused, match="not the action that was approved"):
        executor(store, admin).run(forged)
    assert admin.get("/actions").json() == [] and calls(store) == []


def test_a_stale_copy_of_a_finished_action_sends_nothing(store: Store, admin: TestClient) -> None:
    run = executor(store, admin)
    stale = store.approve("INC-1", "j.meier")  # read while the action was still pending
    assert stale is not None and stale.status == "pending"
    assert run.run(stale) is True
    assert run.run(stale) is True  # the store says it succeeded, whatever the copy says
    assert len(calls(store)) == 1 and len(admin.get("/actions").json()) == 1


def test_a_withheld_proposal_cannot_be_executed(tmp_path: Path, admin: TestClient) -> None:
    s = Store(tmp_path / "held.sqlite")
    put(s, proposal=None)  # the finding proposes the coupon; a check held it
    assert s.get("INC-1").finding.mitigation == COUPON
    assert approve(s, executor(s, admin), "INC-1", "j.meier") == "approved"
    assert admin.get("/actions").json() == []


def test_every_catalogue_action_has_a_route_and_nothing_else_does() -> None:
    catalogue = {action for action in ALLOWED_MITIGATION.values() if action}
    assert set(ROUTES) == catalogue == set(TARGET_KEYS)
    for action, (path, in_body) in ROUTES.items():
        in_path = {part[1:-1] for part in path.split("/") if part.startswith("{")}
        assert in_path | set(in_body) == set(TARGET_KEYS[action])


def test_the_model_cannot_reach_the_executor() -> None:
    # The investigators, the tools and the provider do not import it.
    source = Path(__file__).resolve().parents[1] / "src" / "revwatch"
    readers = [
        *source.glob("investigate/*.py"),
        source / "tools.py",
        source / "llm.py",
        source / "findings.py",
    ]
    assert len(readers) >= 6
    for module in readers:
        text = module.read_text()
        assert "revwatch.actions" not in text and "import actions" not in text, module.name
        assert "httpx" not in text and "mock_admin" not in text, module.name


def test_one_approval_over_a_real_socket(store: Store, tmp_path: Path) -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    app = create_app(tmp_path / "live.sqlite", ENTITIES)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 10
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started
        run = Executor(store, f"http://127.0.0.1:{port}")
        assert approve(store, run, "INC-1", "j.meier") == "mitigated"
        assert httpx.get(f"http://127.0.0.1:{port}/actions").json()[0]["idempotency_key"] == KEY
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    # With the service gone, an approval fails cleanly and stays in the queue.
    put(store, "INC-2")
    assert (
        approve(store, Executor(store, f"http://127.0.0.1:{port}", timeout=1.0), "INC-2", "j.meier")
        == "action_failed"
    )
