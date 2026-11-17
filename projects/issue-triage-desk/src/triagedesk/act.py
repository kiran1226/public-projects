"""What the desk does with a proposal. By default: nothing outside its own log.

Shadow mode records what the desk would have done. With `--apply` it adds labels
and posts one comment, and only on a repository named in `apply_allow`. It never
closes an issue, never removes a label and never writes to a tracker it merely reads.

The write path has tests against a fake tracker. It has never been run against GitHub.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .feed import API
from .gate import MODEL_UNAVAILABLE
from .models import Proposal

MARKER = "<!-- issue-triage-desk -->"
_MENTION = re.compile(r"@(?=\w)")


class NotAllowed(RuntimeError):
    """The repository is not on the allow-list, or no token with write access was given."""


@dataclass
class Plan:
    """The changes a proposal amounts to. Only what the desk is sure of."""

    number: int
    add_labels: list[str] = field(default_factory=list)
    comment: str | None = None

    @property
    def empty(self) -> bool:
        return not self.add_labels and self.comment is None


def plan(number: int, proposal: Proposal, passages: dict[int, str] | None = None) -> Plan:
    """Labels at high confidence, and a comment only for a duplicate at high confidence.

    An answer the gate had to change is not acted on at all: something about it was
    wrong, so a person sees it first. Neither is the classifier's answer when it
    stands in for a model that could not be asked, at the spend cap or after a
    failed call: that issue goes to a person.
    """
    if proposal.gate or MODEL_UNAVAILABLE in proposal.person:
        return Plan(number=number)
    result = Plan(number=number, add_labels=proposal.names("high"))
    if proposal.duplicate_of is not None and proposal.duplicate_confidence == "high":
        passage = (passages or {}).get(proposal.duplicate_of)
        lines = [
            MARKER,
            f"This looks like the same report as #{proposal.duplicate_of}.",
        ]
        if passage:
            # The passage is someone else's text. Without the "@" it cannot notify anyone.
            lines.append("> " + _MENTION.sub("", passage))
        lines.append(
            "This note was written by an automated first pass. A maintainer decides "
            "whether the issue is a duplicate; nothing has been closed."
        )
        result.comment = "\n\n".join(lines)
    return result


class Writer:
    """Applies a plan to a tracker on the allow-list."""

    def __init__(
        self,
        settings: Settings,
        token: str | None,
        transport: httpx.BaseTransport | None = None,
    ):
        if settings.repo not in settings.apply_allow:
            raise NotAllowed(
                f"{settings.repo} is not in apply_allow. The desk writes only to "
                "repositories its configuration names."
            )
        if not token:
            raise NotAllowed("Writing needs a token with write access to issues (GITHUB_TOKEN).")
        self.repo = settings.repo
        self._http = httpx.Client(
            base_url=API,
            transport=transport,
            timeout=60,
            headers={
                "User-Agent": settings.user_agent,
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            },
        )

    def apply(self, planned: Plan) -> list[str]:
        """Carry out a plan. Returns what was done, for the log."""
        done: list[str] = []
        base = f"/repos/{self.repo}/issues/{planned.number}"
        if planned.add_labels:
            response = self._http.post(f"{base}/labels", json={"labels": planned.add_labels})
            response.raise_for_status()
            done.append("labels:" + ",".join(planned.add_labels))
        if planned.comment:
            if self._has_commented(base):
                done.append("comment:already_there")
            else:
                response = self._http.post(f"{base}/comments", json={"body": planned.comment})
                response.raise_for_status()
                done.append("comment")
        return done

    def _has_commented(self, base: str) -> bool:
        """True if the desk's comment is on the issue already. It posts at most one."""
        response = self._http.get(f"{base}/comments", params={"per_page": 100})
        response.raise_for_status()
        return any(MARKER in (comment.get("body") or "") for comment in response.json())
