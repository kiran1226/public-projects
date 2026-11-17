"""Spend ledger: every live call is recorded, and the cap cannot be passed.

Before each call a reserve is held for it. The reserve is an upper bound on what that
one call can cost: the size of the request about to be sent, counted at one input token
per byte (a token is at least one byte, so this over-counts), plus the output limit,
at the highest price the call can be billed at. Calls in flight hold their reserves at
the same time, so parallel cases cannot pass the cap together.

What the bound does not cover: if the API declines a request and a fallback model
answers, the declined attempt is not written to the ledger.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

from .llm import (
    CACHE_WRITE_FACTOR,
    PRICES,
    BudgetExceeded,
    LLMProvider,
    StepRequest,
    StepResponse,
    Usage,
    cost_usd,
    priced_as,
)

# A floor per model, for callers that have no request in hand (`Ledger.check`).
RESERVE = {"claude-opus-5-5": 0.30, "claude-sonnet-5-5": 0.15, "claude-haiku-5-5": 0.02}
UNKNOWN_RESERVE = 0.30
# Tokens the API adds around the content: message framing and its own tool-use preamble.
FRAMING_TOKENS = 2_000


def _worst_rates(model: str) -> tuple[float, float]:
    """Input and output price per million tokens, at the highest rate the call can be
    billed at. A declined Opus request may be answered by a fallback model that costs more."""
    known = priced_as(model)
    rates = [PRICES[known]]
    if known.startswith("claude-opus"):
        rates += [PRICES[name] for name in PRICES if name.startswith("claude-opus")]
    return max(r[0] for r in rates), max(r[1] for r in rates)


def reserve_for(model: str, request: StepRequest | None = None) -> float:
    """An upper bound on the cost of one call to `model`; exact for `request` if given."""
    if "/" in model:  # NVIDIA trial tier: not priced per token
        return 0.0
    floor = next((v for known, v in RESERVE.items() if model.startswith(known)), UNKNOWN_RESERVE)
    if request is None:
        return floor
    body = json.dumps(request.model_dump(exclude={"tag"}), ensure_ascii=False)
    price_in, price_out = _worst_rates(model)
    bound = (
        (len(body.encode("utf-8")) + FRAMING_TOKENS) * price_in * max(1.0, CACHE_WRITE_FACTOR)
        + request.max_tokens * price_out
    ) / 1_000_000
    return max(floor, bound)


class Ledger:
    def __init__(self, path: Path, cap_usd: float = 5.00, run_cap_usd: float | None = None):
        self.path, self.cap_usd, self.run_cap_usd = Path(path), cap_usd, run_cap_usd
        self._lock = threading.Lock()
        self._run_spent = 0.0
        self._held = 0.0  # reserves of calls in flight

    def spent(self) -> float:
        if not self.path.exists():
            return 0.0
        total = 0.0
        for number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                cost = float(json.loads(line)["cost_usd"])
                if not math.isfinite(cost) or cost < 0:
                    raise ValueError("not a cost")
            except (ValueError, KeyError, TypeError) as exc:
                # Never treat an unreadable ledger as zero spend: stop the run, as the cap does.
                raise BudgetExceeded(
                    f"The spend ledger is damaged at line {number}: {self.path}. "
                    "Repair or remove it before another live run."
                ) from exc
            total += cost
        return total

    def reserve(self, model: str, request: StepRequest | None = None) -> float:
        """Hold an upper bound on the cost of one call, or stop the run if it does not fit.
        Returns the amount held, to hand back to `release`."""
        amount = reserve_for(model, request)
        with self._lock:
            if self.spent() + self._held + amount > self.cap_usd:
                raise BudgetExceeded(f"Budget cap of ${self.cap_usd:.2f} reached.")
            if (
                self.run_cap_usd is not None
                and self._run_spent + self._held + amount > self.run_cap_usd
            ):
                raise BudgetExceeded(f"Run cap of ${self.run_cap_usd:.2f} reached.")
            self._held += amount
        return amount

    def release(self, model: str, amount: float | None = None) -> None:
        with self._lock:
            held = reserve_for(model) if amount is None else amount
            self._held = max(0.0, self._held - held)

    def check(self, model: str) -> None:
        """Would one more call fit right now?"""
        self.reserve(model)
        self.release(model)

    def record(self, model: str, usage: Usage, tag: str) -> float:
        cost = cost_usd(model, usage)
        line = json.dumps(
            {"tag": tag, "model": model, "cost_usd": round(cost, 6), **usage.model_dump()}
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
            self._run_spent += cost
        return cost


class BudgetedProvider:
    """Wraps a live provider: reserve before the call, record after it, then release."""

    def __init__(self, inner: LLMProvider, ledger: Ledger):
        self.inner, self.ledger = inner, ledger

    def step(self, request: StepRequest) -> StepResponse:
        held = self.ledger.reserve(request.model, request)
        try:
            response = self.inner.step(request)
            self.ledger.record(response.model, response.usage, request.tag)
        finally:
            self.ledger.release(request.model, held)
        return response
