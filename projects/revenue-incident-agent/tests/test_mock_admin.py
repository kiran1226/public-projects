from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from mock_admin.app import create_app
from mock_admin.seed import entities_from_world
from revwatch.scenarios import Label

ENTITIES = {
    "coupon": {"SOMMER15"}, "release": {"android-5.14.0"}, "sku": {"LIG-40417"},
    "provider": {"psp_alpha"}, "campaign": {"ps-es-brand-q4"},
    "country": {"DE", "FR"}, "channel": {"affiliate", "email"},
}  # fmt: skip
WHO = {"incident_id": "INC-1", "reviewer": "j.meier"}


@pytest.fixture()
def admin(tmp_path: Path) -> TestClient:
    return TestClient(create_app(tmp_path / "var" / "admin.sqlite", ENTITIES))


def post(admin: TestClient, path: str, key: str = "k1", **body: str) -> tuple[int, dict]:  # type: ignore[type-arg]
    response = admin.post(path, json={**WHO, **body}, headers={"Idempotency-Key": key})
    return response.status_code, response.json()


@pytest.mark.parametrize(
    ("path", "body", "action", "target", "state"),
    [
        ("/coupons/SOMMER15/disable", {}, "disable_coupon", {"code": "SOMMER15"}, "disabled"),
        (
            "/releases/android-5.14.0/rollback",
            {},
            "rollback_release",
            {"release_id": "android-5.14.0"},
            "rolled_back",
        ),
        ("/listings/LIG-40417/pause", {}, "pause_listing", {"sku": "LIG-40417"}, "paused"),
        (
            "/payment-providers/psp_alpha/failover",
            {"country": "DE"},
            "failover_payment_provider",
            {"provider": "psp_alpha", "country": "DE"},
            "failed_over",
        ),
        (
            "/bot-challenge",
            {"country": "FR", "channel": "affiliate"},
            "enable_bot_challenge",
            {"country": "FR", "channel": "affiliate"},
            "enabled",
        ),
        (
            "/campaigns/ps-es-brand-q4/resume",
            {},
            "resume_campaign",
            {"campaign_id": "ps-es-brand-q4"},
            "active",
        ),
    ],
)
def test_each_mitigation_has_an_endpoint_that_applies_it(
    admin: TestClient,
    path: str,
    body: dict[str, str],
    action: str,
    target: dict[str, str],
    state: str,
) -> None:
    code, answer = post(admin, path, **body)
    assert code == 200
    assert answer == {"action": action, "target": target, "state": state, "changed": True}
    applied = admin.get("/actions").json()
    assert len(applied) == 1
    assert (applied[0]["action"], applied[0]["target"]) == (action, target)
    assert (applied[0]["incident_id"], applied[0]["reviewer"], applied[0]["idempotency_key"]) == (
        "INC-1",
        "j.meier",
        "k1",
    )


def test_the_same_key_returns_the_first_response_and_acts_once(admin: TestClient) -> None:
    first = post(admin, "/coupons/SOMMER15/disable")
    again = post(admin, "/coupons/SOMMER15/disable")
    assert (
        first
        == again
        == (
            200,
            {
                "action": "disable_coupon",
                "target": {"code": "SOMMER15"},
                "state": "disabled",
                "changed": True,
            },
        )
    )
    assert len(admin.get("/actions").json()) == 1


def test_a_target_already_in_the_requested_state_is_not_changed_again(admin: TestClient) -> None:
    post(admin, "/coupons/SOMMER15/disable", key="k1")
    code, answer = post(admin, "/coupons/SOMMER15/disable", key="k2")
    assert (code, answer["changed"], answer["state"]) == (200, False, "disabled")
    assert len(admin.get("/actions").json()) == 1


def test_a_key_reused_for_a_different_request_is_a_conflict(admin: TestClient) -> None:
    post(admin, "/coupons/SOMMER15/disable", key="k1")
    code, answer = post(admin, "/listings/LIG-40417/pause", key="k1")
    assert code == 409 and "different request" in answer["detail"]
    assert len(admin.get("/actions").json()) == 1


def test_an_unknown_target_is_not_found_and_nothing_is_applied(admin: TestClient) -> None:
    assert post(admin, "/coupons/NOPE/disable") == (404, {"detail": "unknown coupon: NOPE"})
    assert post(admin, "/payment-providers/psp_alpha/failover", key="k2", country="XX")[0] == 404
    assert post(admin, "/payment-providers/psp_alpha/failover", key="k3")[0] == 404  # no country
    assert post(admin, "/bot-challenge", key="k4", country="FR", channel="carrier-pigeon")[0] == 404
    assert admin.get("/actions").json() == []
    # The same key gives the same answer again.
    assert post(admin, "/coupons/NOPE/disable") == (404, {"detail": "unknown coupon: NOPE"})


def test_a_request_without_a_key_or_with_unknown_fields_is_refused(admin: TestClient) -> None:
    no_key = admin.post("/coupons/SOMMER15/disable", json=WHO)
    assert no_key.status_code == 400 and "Idempotency-Key" in no_key.json()["detail"]
    extra = admin.post(
        "/coupons/SOMMER15/disable", json={**WHO, "force": True}, headers={"Idempotency-Key": "k"}
    )
    assert extra.status_code == 422
    nobody = admin.post(
        "/coupons/SOMMER15/disable", json={"incident_id": "INC-1"}, headers={"Idempotency-Key": "k"}
    )
    assert nobody.status_code == 422
    assert admin.get("/actions").json() == []


def test_state_and_keys_survive_a_restart(tmp_path: Path) -> None:
    path = tmp_path / "admin.sqlite"
    first = TestClient(create_app(path, ENTITIES))
    answer = post(first, "/releases/android-5.14.0/rollback")
    restarted = TestClient(create_app(path, ENTITIES))
    assert post(restarted, "/releases/android-5.14.0/rollback") == answer
    assert post(restarted, "/releases/android-5.14.0/rollback", key="k2")[1]["changed"] is False
    assert len(restarted.get("/actions").json()) == 1


def test_health_reports_what_the_service_was_seeded_with(admin: TestClient) -> None:
    health = admin.get("/health").json()
    assert (
        health["status"] == "ok"
        and health["entities"]["coupon"] == 1
        and health["entities"]["country"] == 2
    )


def test_the_service_is_seeded_from_a_world_file(small_world: tuple[Path, list[Label]]) -> None:
    entities = entities_from_world(small_world[0])
    assert "psp_alpha" in entities["provider"] and len(entities["provider"]) == 5
    assert entities["country"] == {"DE", "FR", "NL", "AT", "PL", "ES"}
    assert "affiliate" in entities["channel"]
    assert entities["coupon"] and entities["release"] and entities["sku"] and entities["campaign"]
