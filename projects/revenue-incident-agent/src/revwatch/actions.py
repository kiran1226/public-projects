"""The executor: the only code that changes anything outside the project's own store.

It runs when a reviewer approves an incident whose proposal passed every check.
The model never calls it and has no tool for it; the investigators do not import
this module.

Acting once. The approval and a pending action are already in the store when
the request is sent (`Store.approve` writes both in one transaction). The
request carries an idempotency key derived from the incident, the action and
the target. If this process dies between the call and the record, the action is
still pending, the retry carries the same key, and the service returns its
first response without acting again. A local "already done" flag alone cannot
cover that crash.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from revwatch.scenarios import TARGET_KEYS, Mitigation
from revwatch.store import PendingAction, Store, StoreError

DEFAULT_URL = "http://127.0.0.1:8765"
TIMEOUT_SECONDS = 5.0
RETRIES = 1  # a connection error, a timeout or a 5xx is tried once more, with the same key
# The states an incident can be in once a reviewer approved a proposal.
APPROVED = ("action_pending", "action_failed", "mitigated")

# Action -> (path, target keys that travel in the body and not in the path).
ROUTES: dict[str, tuple[str, tuple[str, ...]]] = {
    "disable_coupon": ("/coupons/{code}/disable", ()),
    "rollback_release": ("/releases/{release_id}/rollback", ()),
    "pause_listing": ("/listings/{sku}/pause", ()),
    "failover_payment_provider": ("/payment-providers/{provider}/failover", ("country",)),
    "enable_bot_challenge": ("/bot-challenge", ("country", "channel")),
    "resume_campaign": ("/campaigns/{campaign_id}/resume", ()),
}


class Refused(StoreError):
    """The executor will not send this, whoever asks."""


class Executor:
    def __init__(
        self,
        store: Store,
        base_url: str = DEFAULT_URL,
        client: httpx.Client | None = None,
        timeout: float = TIMEOUT_SECONDS,
    ) -> None:
        self.store = store
        self.base_url = base_url.rstrip("/")
        # A client passed in keeps its own timeout.
        self.client = client or httpx.Client(timeout=timeout)

    def run(self, action: PendingAction) -> bool:
        """Send an approved action. True if the service confirmed it."""
        action = self._check(action)
        if action.status == "succeeded":
            return True  # recorded as done: nothing is sent twice
        path, in_body = ROUTES[action.action]
        url = self.base_url + path.format(**action.target)
        body = {
            "incident_id": action.incident_id,
            "reviewer": action.reviewer,
            **{key: action.target[key] for key in in_body},
        }
        detail = "no attempt was made"
        for attempt in range(1, RETRIES + 2):
            record: dict[str, Any] = {
                "method": "POST",
                "path": url,
                "body": body,
                "idempotency_key": action.idempotency_key,
                "attempt": action.attempts + attempt,
            }
            started = time.perf_counter()
            try:
                response = self.client.post(
                    url,
                    json=body,
                    headers={"Idempotency-Key": action.idempotency_key},
                )
            except httpx.TransportError as exc:  # connection refused, timeout, broken socket
                detail = f"admin service not reached: {type(exc).__name__}"
                self.store.log_attempt(action, {**record, "error": detail, **_took(started)})
                continue
            answer = _json(response)
            self.store.log_attempt(
                action,
                {
                    **record,
                    "status_code": response.status_code,
                    "response": answer,
                    **_took(started),
                },
            )
            if response.is_success:
                changed = isinstance(answer, dict) and answer.get("changed")
                done = "applied" if changed else "already in the requested state"
                self.store.finish_action(action, True, done)
                return True
            detail = f"admin service returned {response.status_code}: {_message(answer)}"
            if response.status_code < 500:
                break  # the request itself is wrong; sending it again cannot help
        self.store.finish_action(action, False, detail)
        return False

    def _check(self, action: PendingAction) -> PendingAction:
        """Refuse anything that did not pass the guardrails or was not approved.

        Checked here again, whoever calls: the catalogue, the incident's own
        proposal, and the approval. Returns the action as the store holds it,
        so a stale copy cannot send what is already done.
        """
        if action.action not in ROUTES:
            raise Refused(f"{action.action} is not in the mitigation catalogue")
        if sorted(action.target) != sorted(TARGET_KEYS[action.action]):
            raise Refused(f"{action.action} needs target {', '.join(TARGET_KEYS[action.action])}")
        wanted = Mitigation.model_validate({"action": action.action, "target": action.target})
        record = self.store.get(action.incident_id)
        if record.proposal != wanted:
            raise Refused(
                f"{action.incident_id} carries no approved proposal for {action.action} "
                f"on {action.target}"
            )
        # `Store.approve` writes the decision and the action row in one transaction.
        stored = self.store.action_for(action.incident_id)
        if stored is None or record.status not in APPROVED:
            raise Refused(f"{action.incident_id} has not been approved by a reviewer")
        same = (stored.idempotency_key, stored.action, stored.target) == (
            action.idempotency_key,
            action.action,
            action.target,
        )
        if not same:
            raise Refused(f"this is not the action that was approved for {action.incident_id}")
        return stored


def approve(
    store: Store, executor: Executor, incident_id: str, reviewer: str, note: str = ""
) -> str:
    """A reviewer's approval, carried through: decide, execute if there is a proposal.

    Returns the incident's status afterwards. Approving again after a failure
    tries again with the same key; approving again after success sends nothing.
    """
    action = store.approve(incident_id, reviewer, note)
    if action is not None:
        executor.run(action)
    return store.get(incident_id).status


def _took(started: float) -> dict[str, float]:
    return {"latency_ms": round(1000 * (time.perf_counter() - started), 1)}


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return response.text[:500]


def _message(answer: Any) -> str:
    return str(answer.get("detail", answer)) if isinstance(answer, dict) else str(answer)
