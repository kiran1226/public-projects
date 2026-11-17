"""A daily spend cap, spent evenly across the day.

A plain cap would be gone by breakfast on a busy day and leave the evening's edits
unread. So the allowance grows through the day: at noon half the cap is available,
plus a little headroom so that the first calls of the day are not refused. When the
allowance is used up, edits keep their tier-1 score and the refusal is counted.
"""

from __future__ import annotations

import threading

DAY_S = 86_400.0


class PacedBudget:
    def __init__(self, cap_usd: float, spent_usd: float = 0.0, headroom_share: float = 1 / 24):
        self.cap_usd = cap_usd
        self.headroom_usd = cap_usd * headroom_share
        self._spent = spent_usd
        self._lock = threading.Lock()
        self.refused = 0

    @property
    def spent_usd(self) -> float:
        return self._spent

    def allowance(self, seconds_into_day: float) -> float:
        """What may have been spent by this time of day."""
        share = min(1.0, max(0.0, seconds_into_day / DAY_S))
        return min(self.cap_usd, self.cap_usd * share + self.headroom_usd)

    def allows(self, seconds_into_day: float) -> bool:
        """True if another call may be made now. A refusal is counted."""
        with self._lock:
            if self._spent >= self.allowance(seconds_into_day):
                self.refused += 1
                return False
            return True

    def add(self, cost_usd: float) -> None:
        with self._lock:
            self._spent += cost_usd
