"""A spend cap checked before every model call.

The scheduled job runs unattended, so the cap is enforced in code, not by attention.
When it is reached the run goes on without the model and counts what it skipped.
"""

from __future__ import annotations

import threading


class Budget:
    def __init__(self, cap_usd: float, spent_usd: float = 0.0):
        self.cap_usd = cap_usd
        self._spent = spent_usd
        self._lock = threading.Lock()
        self.refused = 0

    @property
    def spent_usd(self) -> float:
        return self._spent

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self._spent)

    def allows(self) -> bool:
        """True if another call may be made. A refusal is counted."""
        with self._lock:
            if self._spent >= self.cap_usd:
                self.refused += 1
                return False
            return True

    def add(self, cost_usd: float) -> None:
        with self._lock:
            self._spent += cost_usd
