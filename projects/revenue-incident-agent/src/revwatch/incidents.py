"""Group alarms into incidents and name the slice each incident is about.

One event raises many alarms: a card outage in Germany pulls down Germany, card
payments and the total with it, and a promotion lifts every slice at once. Each
tick the alarms of a metric are therefore first explained by one another:

- A slice is *carried by a broader one* when it moved about as much as the rest
  of that broader slice did. It is part of the same thing, not a thing of its own.
- A slice is *carried by a narrower one* when, with that narrower slice taken
  out, what is left of it is inside its expected band. It only moved because the
  narrower slice did.

What nothing else explains is a *root*. A root and everything it carries are one
part of an incident; parts on different metrics are joined when their roots are
the same place. An open incident takes new alarms through its roots, so an
alert about a promotion does not swallow an outage that starts inside it.

Two things keep one event from being reported again an hour later. A slice that
several roots explain only together belongs to all of them, so an alert about
the whole goes on when an even lift turns uneven. And a root that is only
weakly out of band stays with the incident it was alarming in a moment ago.

The idea that a cause moves everything beneath it in proportion is the "ripple
effect" used for root-cause localisation in multi-dimensional metrics.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from revwatch.models import Alarm, FloatArray, Incident, Member, Slice
from revwatch.policy import DetectionPolicy


@dataclass(frozen=True)
class Reading:
    """One metric at one tick, one entry per slice.

    `deviation`, `expected`, `volume` and `strength` cover the same few ticks up
    to this one, so they can be compared with one another.
    """

    deviation: FloatArray  # observed minus expected numerator
    expected: FloatArray  # expected numerator
    volume: FloatArray | None  # denominator, for a rate; None for a count
    strength: FloatArray  # mean signed strength; beyond 1 is outside the detector's band
    pvalue: FloatArray  # at this tick


@dataclass(frozen=True)
class _Amount:
    """Deviation, expected numerator and denominator of a slice, or of what is left of one."""

    deviation: float
    expected: float
    volume: float

    def less(self, other: _Amount) -> _Amount:
        return _Amount(
            self.deviation - other.deviation,
            self.expected - other.expected,
            self.volume - other.volume,
        )


@dataclass
class _Part:
    """A root on one metric and direction, with the alarms it explains."""

    metric: str
    direction: int
    root: Slice
    slices: set[Slice]
    alarms: list[Alarm]
    view: _View  # the readings it was found in
    # Whether the root is clearly a thing of its own: the part of its move that nothing
    # broader explains is well outside its band.
    distinct: bool


@dataclass
class _Verdict:
    """What one pass over the slices of a metric and direction decided."""

    roots: dict[Slice, float] = field(default_factory=dict)  # how far out of band on its own
    carrier: dict[Slice, Slice] = field(default_factory=dict)
    jointly: dict[Slice, list[Slice]] = field(default_factory=dict)  # further roots that carry it
    muted: set[Slice] = field(default_factory=set)  # carried, but no handle for an open incident


@dataclass
class _Group:
    parts: list[_Part] = field(default_factory=list)
    incidents: list[Incident] = field(default_factory=list)

    @property
    def alarms(self) -> list[Alarm]:
        return [alarm for part in self.parts for alarm in part.alarms]


def _disjoint(a: Slice, b: Slice) -> bool:
    """True when no row can be in both: they name different values of some dimension."""
    mine = dict(a.items)
    return any(dim in mine and mine[dim] != value for dim, value in b.items)


def _ratio(explained: float, seen: float) -> float:
    """How much of a move `seen` the move `explained` accounts for, between 0 and 1."""
    if seen <= 0:
        return 1.0  # nothing left to explain
    return float(np.clip(explained / seen, 0.0, 1.0))


class _View:
    """The readings of one metric at one tick, seen in one direction.

    Deviations and strengths are multiplied by the direction, so "more of what
    the alarm is about" is positive whether the alarm is a rise or a fall.
    """

    def __init__(
        self,
        reading: Reading,
        index: Mapping[Slice, int],
        direction: int,
        against: Sequence[Slice] = (),
    ) -> None:
        self._reading, self._index, self.direction = reading, index, direction
        # Roots that moved the other way. They are other incidents, so they are taken
        # out of every slice that contains them before that slice is judged: a total
        # that is up 30% everywhere except where something broke is up 30%.
        self._against = tuple(against)
        # A count has one way to move as its broader slice does: by the same share. A
        # rate has two: by the same share (success x 0.5) or by the same points (discount + 0.1).
        self.measures: tuple[Callable[[_Amount], float], ...] = (
            (self.relative,) if reading.volume is None else (self.relative, self.absolute)
        )

    def amount(self, where: Slice) -> _Amount:
        amount = self._raw(where)
        chosen: list[Slice] = []
        inside = [r for r in self._against if r != where and where.contains(r)]
        for root in sorted(inside, key=lambda r: (self._raw(r).deviation, r.key)):
            if all(_disjoint(root, other) for other in chosen):
                chosen.append(root)
                amount = amount.less(self._raw(root))
        return amount

    def _raw(self, where: Slice) -> _Amount:
        i = self._index[where]
        volume = self._reading.volume
        return _Amount(
            self._clean(self._reading.deviation[i]),
            float(np.nan_to_num(self._reading.expected[i])),
            0.0 if volume is None else float(np.nan_to_num(volume[i])),
        )

    def strength(self, where: Slice) -> float:
        """How far out of its band the slice is in this direction; 0 if it is the other way."""
        return max(0.0, self._clean(self._reading.strength[self._index[where]]))

    def _clean(self, value: float) -> float:
        value = float(value) * self.direction
        return value if np.isfinite(value) else 0.0

    @staticmethod
    def relative(amount: _Amount) -> float:
        return amount.deviation / amount.expected if amount.expected > 0 else 0.0

    @staticmethod
    def absolute(amount: _Amount) -> float:
        return amount.deviation / amount.volume if amount.volume > 0 else 0.0


class Grouper:
    """Turns the alarms of each tick into incidents. Call `step` once per tick, in order."""

    def __init__(
        self,
        policy: DetectionPolicy,
        world_hash: str,
        slices: Mapping[str, Sequence[Slice]],
        read: Callable[[str, int], Reading],
    ) -> None:
        self.policy = policy
        self.world_hash = world_hash
        self.read = read
        self.incidents: list[Incident] = []
        self._index = {
            metric: {candidate: i for i, candidate in enumerate(items)}
            for metric, items in slices.items()
        }
        self._dims = {
            metric: frozenset(dim for candidate in items for dim, _ in candidate.items)
            for metric, items in slices.items()
        }
        self._parents: dict[tuple[str, Slice], tuple[Slice, ...]] = {}

    def step(self, tick: int, alarms: Sequence[Alarm]) -> list[Incident]:
        """Process one tick. Returns the incidents opened at this tick."""
        for incident in self.incidents:
            quiet = tick - incident.last_alarm_tick
            if incident.closed_tick is None and quiet > self.policy.close_after_quiet_hours:
                incident.closed_tick = incident.last_alarm_tick + 1
        open_ = [incident for incident in self.incidents if incident.closed_tick is None]
        if not alarms:
            return []

        parts, live = self._parts(tick, alarms, open_)
        opened: list[Incident] = []
        for group in self._groups(tick, parts, open_, live):
            found = group.alarms
            if not found:
                continue
            if group.incidents:
                # The oldest survives; of two opened in the same tick, the one with more evidence.
                target = min(
                    group.incidents, key=lambda i: (i.opened_tick, -len(i.members), i.incident_id)
                )
                for other in group.incidents:
                    if other is not target:
                        self._merge(other, target, tick)
            else:
                target = self._open(tick, group.parts, found)
                opened.append(target)
            for alarm in found:
                target.members.add(alarm.member)
                target.last_seen[alarm.member] = tick
            # A root is current while it explains alarms, whether or not it alarms itself.
            for part in group.parts:
                if part.alarms:
                    target.last_seen[(part.metric, part.root, part.direction)] = tick
            target.last_alarm_tick = tick
            # The roots of the metrics seen this tick replace the earlier ones for those metrics.
            seen = {(part.metric, part.direction) for part in group.parts}
            target.roots = {root for root in target.roots if (root[0], root[2]) not in seen} | {
                (part.metric, part.root, part.direction) for part in group.parts
            }
        return opened

    def _merge(self, other: Incident, target: Incident, tick: int) -> None:
        """Fold `other` into `target`. `other` was raised, so it stays on record, closed."""
        target.members |= other.members
        target.roots |= other.roots
        for member, seen in other.last_seen.items():
            target.last_seen[member] = max(seen, target.last_seen.get(member, seen))
        other.merged_into = target.incident_id
        other.closed_tick = tick

    # --- explaining the alarms of one metric by one another --------------------

    def _parts(
        self, tick: int, alarms: Sequence[Alarm], open_: Sequence[Incident]
    ) -> tuple[list[_Part], set[Member]]:
        """The parts of this tick, and the roots of open incidents that still stand."""
        alarming: dict[tuple[str, int], dict[Slice, Alarm]] = {}
        for alarm in alarms:
            alarming.setdefault((alarm.metric, alarm.direction), {})[alarm.slice] = alarm
        # Roots of open incidents, and whether each was a root or alarmed within the link gap.
        gap = self.policy.link_gap_hours
        kept: dict[tuple[str, int], dict[Slice, bool]] = {}
        for incident in open_:
            for root in incident.roots:
                metric, where, direction = root
                recent = tick - incident.last_seen.get(root, tick - gap - 1) <= gap
                known = kept.setdefault((metric, direction), {})
                known[where] = known.get(where, False) or recent

        parts: list[_Part] = []
        live: set[Member] = set()
        keys = set(alarming) | set(kept)
        for metric in sorted({metric for metric, _ in keys}):
            reading, index = self.read(metric, tick), self._index[metric]
            directions = [d for d in (-1, 1) if (metric, d) in keys]
            found: dict[int, list[_Part]] = {}
            # Twice when the metric moves both ways: the second time each direction knows
            # the roots of the other and judges its slices without them.
            for _ in range(len(directions)):
                against = {d: [p.root for p in found.get(-d, []) if p.alarms] for d in directions}
                for direction in directions:
                    view = _View(reading, index, direction, against[direction])
                    mine = alarming.get((metric, direction), {})
                    # A root stands while it alarms, or for a few ticks after while it is
                    # still out of its band. Later it has nothing to say about this tick.
                    standing = {
                        where: recent
                        for where, recent in kept.get((metric, direction), {}).items()
                        if where in mine or (recent and view.strength(where) >= 1.0)
                    }
                    found[direction] = self._explain(metric, direction, view, mine, standing)
                    live |= {(metric, where, direction) for where in standing}
            for direction in directions:
                parts += found[direction]
        return parts, live

    def _explain(
        self,
        metric: str,
        direction: int,
        view: _View,
        alarming: Mapping[Slice, Alarm],
        standing: Mapping[Slice, bool],
    ) -> list[_Part]:
        firm = set(alarming) | set(standing)
        # A broader slice that is out of its band can explain an alarm inside it, even
        # though it did not pass false-alarm control itself.
        hinted = {
            parent
            for where in alarming
            for parent in self._ancestors(metric, where)
            if parent not in firm and view.strength(parent) >= 1.0
        }

        def strength(where: Slice) -> float:
            """How far out of its band. A slice that alarms is at least as far out as its
            alarm says, even if the hours before it pull the average back inside."""
            alarm = alarming.get(where)
            return max(view.strength(where), abs(alarm.strength) if alarm else 0.0)

        # Narrowest first: a narrow root has to be known before the slices above it are
        # judged. Among equals the strongest first, so the rest are judged without it.
        order = sorted(firm | hinted, key=lambda s: (-len(s.items), -strength(s), s.key))
        # Twice. A narrow slice is compared with the rest of each broader slice, and that
        # rest may hold another incident on a slice that is judged later (France on
        # Android, and Germany). The second time the roots of the first are known.
        verdict = _Verdict()
        for _ in range(2):
            verdict = self._judge(metric, view, order, firm, hinted, standing, strength, verdict)

        found = {
            root: _Part(
                metric,
                direction,
                root,
                {root},
                [],
                view,
                distinct=left >= self.policy.separate_strength,
            )
            for root, left in verdict.roots.items()
        }
        for where in order:
            root = where
            while root in verdict.carrier:
                root = verdict.carrier[root]
            # An open incident claims a part through its root, or through a slice that
            # alarms or still stands; not through one that is merely out of band.
            if where in firm and where not in verdict.muted:
                found[root].slices.add(where)
                # A slice that several roots account for only together belongs to each of
                # their parts: an alert about the whole goes on as all of them when an
                # even lift turns uneven. The parts are not joined to one another by it.
                for other in verdict.jointly.get(where, ()):
                    found[other].slices.add(where)
            if where in alarming:
                found[root].alarms.append(alarming[where])
        return [part for part in found.values() if part.alarms or part.slices & set(standing)]

    def _judge(
        self,
        metric: str,
        view: _View,
        order: Sequence[Slice],
        firm: set[Slice],
        hinted: set[Slice],
        standing: Mapping[Slice, bool],
        strength: Callable[[Slice], float],
        before: _Verdict,
    ) -> _Verdict:
        """One pass over the slices: each is a root, or carried by a narrower or a broader one.

        `before` is the verdict of the pass before this one. Its roots are other
        incidents wherever they lie outside the slice being judged.
        """
        verdict = _Verdict()
        roots: list[Slice] = []
        for where in order:
            whole = view.amount(where)
            # The hours behind the alarm say the opposite of the alarm. There is nothing
            # to explain it by, and nothing it explains.
            if whole.deviation < 0:
                roots.append(where)
                verdict.roots[where] = strength(where)
                continue
            inside = self._largest_disjoint([r for r in roots if where.contains(r)], view)
            rest = whole
            for root in inside:
                rest = rest.less(view.amount(root))
            share = _ratio(rest.deviation, whole.deviation)
            own = strength(where) * share
            lapsed = where in standing and not standing[where]
            if inside and (own < 1.0 or share <= 1.0 - self.policy.explain_share):
                verdict.carrier[where] = inside[0]
                verdict.jointly[where] = inside[1:]
                # Not a way for an open incident to claim what carries it: the root of an
                # alert that went quiet hours ago, or a slice that is still out of band
                # by itself beside the narrower root. A promotion that is still running
                # does not take the outage that now makes up most of the total.
                if lapsed or own >= 1.0:
                    verdict.muted.add(where)
                continue
            elsewhere = [*roots, *(r for r in before.roots if r not in roots)]
            best, left = self._carried_by(
                metric, view, where, whole, rest, own, elsewhere, firm, hinted
            )
            if best is None:
                roots.append(where)
                verdict.roots[where] = left
            else:
                verdict.carrier[where] = best
                if lapsed:
                    verdict.muted.add(where)
        return verdict

    def _carried_by(
        self,
        metric: str,
        view: _View,
        where: Slice,
        whole: _Amount,
        rest: _Amount,
        own: float,
        roots: Sequence[Slice],
        firm: set[Slice],
        hinted: set[Slice],
    ) -> tuple[Slice | None, float]:
        """The broader slice whose move explains `where`, if any does, and what is left.

        `rest` is `where` without the roots inside it and `own` is how far that is
        out of band. The second value returned is how far out of band the part of
        `where` is that no broader slice explains.

        Each broader slice is looked at outside `where`, and without the roots
        found elsewhere in it: those are other incidents. What is compared is the
        move as a share of expected and, for a rate, also in points: a failing
        provider halves the success rate of every slice it serves, a coupon adds
        the same ten points of discount to each.
        """
        outside: dict[Slice, _Amount] = {}
        for parent in self._ancestors(metric, where):
            if parent not in firm and parent not in hinted:
                continue
            others = [r for r in roots if parent.contains(r) and _disjoint(r, where)]
            amount = view.amount(parent).less(whole)
            for root in self._largest_disjoint(others, view):
                amount = amount.less(view.amount(root))
            outside[parent] = amount
        if not outside:
            return None, own

        def explained(parent: Slice) -> float:
            return max(_ratio(m(outside[parent]), m(rest)) for m in view.measures)

        best = max(outside, key=lambda p: (explained(p), p in firm, -len(p.items), p.key))
        share = explained(best)
        left = own * (1.0 - share)
        # A thing of its own: most of its move is unexplained, and that part alone is
        # well outside its band. Either test alone fails on a large event, where every
        # slice is far out of band and none matches its broader slices to the percent.
        if share < self.policy.ripple_share:
            if left >= self.policy.separate_strength:
                return None, left
            # A broader slice that is not alarming itself has to show a move of its own.
            if best not in firm:
                return None, left
        return best, left

    def _largest_disjoint(self, roots: Iterable[Slice], view: _View) -> list[Slice]:
        """The roots with the largest deviations that do not overlap, largest first."""
        chosen: list[Slice] = []
        for root in sorted(roots, key=lambda r: (-view.amount(r).deviation, r.key)):
            if all(_disjoint(root, other) for other in chosen):
                chosen.append(root)
        return chosen

    def _ancestors(self, metric: str, where: Slice) -> tuple[Slice, ...]:
        key = (metric, where)
        if key not in self._parents:
            self._parents[key] = tuple(
                other for other in self._index[metric] if other != where and other.contains(where)
            )
        return self._parents[key]

    # --- joining parts across metrics and with open incidents -------------------

    def _same_place(self, a: _Part, b: _Part) -> bool:
        """Whether two roots on different metrics are the same slice of what both metrics have.

        A card failure in France (payments, by country and payment method) and a
        conversion drop in France are both "France" there. Two roots that are both
        the whole business are the same place too; two that merely have nothing
        in common (card payments, and the email channel) are not.
        """
        if a.metric == b.metric:
            return False
        shared = self._dims[a.metric] & self._dims[b.metric]
        seen = a.root.project(shared)
        if seen != b.root.project(shared):
            return False
        return bool(seen.items) or not (a.root.items or b.root.items)

    def _groups(
        self, tick: int, parts: Sequence[_Part], open_: Sequence[Incident], live: set[Member]
    ) -> list[_Group]:
        """Connected components over the parts and the open incidents they belong to."""
        parent = list(range(len(parts) + len(open_)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, part in enumerate(parts):
            for j in range(i):
                if self._same_place(part, parts[j]):
                    parent[find(i)] = find(j)
            for k, incident in enumerate(open_):
                if any(
                    metric == part.metric and direction == part.direction and where in part.slices
                    for metric, where, direction in incident.roots
                ):
                    parent[find(i)] = find(len(parts) + k)

        # A part that no incident claimed through a root can still be a continuation of
        # the incident one of its slices was alarming in a moment ago:
        # - when its own root is only weakly out of band. The slice that used to carry
        #   it fell back into its band first, so nothing broader is left to explain it.
        # - when it is clearly a thing of its own, only if the incident's roots on this
        #   metric have gone quiet and its root contains one of them: the narrow root
        #   dropped out of scoring and the broader slices it dragged along still alarm.
        #   A narrower slice that turns strong is something new.
        holder = {find(len(parts) + k) for k in range(len(open_))}
        gap = self.policy.link_gap_hours
        for i, part in enumerate(parts):
            if find(i) in holder or not part.alarms:
                continue
            resting: list[tuple[int, Incident]] = []
            for k, incident in enumerate(open_):
                if not any(
                    tick - incident.last_seen.get(a.member, tick - gap - 1) <= gap
                    for a in part.alarms
                ):
                    continue
                mine = [
                    root
                    for root in incident.roots
                    if root[0] == part.metric and root[2] == part.direction
                ]
                dragged = not any(root in live for root in mine) and any(
                    part.root.contains(where) for _, where, _ in mine
                )
                if dragged or not part.distinct:
                    resting.append((k, incident))
            if resting:
                k, _ = max(
                    resting, key=lambda item: (item[1].last_alarm_tick, -item[1].opened_tick)
                )
                parent[find(i)] = find(len(parts) + k)
                holder.add(find(i))

        groups: dict[int, _Group] = {}
        for i in range(len(parent)):
            group = groups.setdefault(find(i), _Group())
            if i < len(parts):
                group.parts.append(parts[i])
            else:
                group.incidents.append(open_[i - len(parts)])
        return list(groups.values())

    # --- opening and naming ----------------------------------------------------

    def _open(self, tick: int, parts: Sequence[_Part], alarms: Sequence[Alarm]) -> Incident:
        primary = max(alarms, key=lambda a: (abs(a.strength), a.metric, a.slice.key))
        part = next(p for p in parts if primary in p.alarms)
        # Every alarm belongs to one group, so the alarms that open an incident identify it.
        evidence = ";".join(sorted(f"{m}:{s.key}:{d}" for m, s, d in {a.member for a in alarms}))
        digest = hashlib.sha1(f"{self.world_hash}|{tick}|{evidence}".encode()).hexdigest()
        incident = Incident(
            incident_id=f"INC-{digest[:12]}",
            opened_tick=tick,
            metric=part.metric,
            slice=self._localise(tick, part),
            direction=part.direction,
            last_alarm_tick=tick,
        )
        self.incidents.append(incident)
        return incident

    def _localise(self, tick: int, part: _Part) -> Slice:
        """Name the incident: the narrowest slice that holds most of the root's deviation.

        The root is the narrowest slice that nothing explains. It alarmed, or it is
        out of its band and explains a slice that did. A slice inside it may hold
        nearly all of its deviation without alarming, for instance because it is
        under the volume floor. Of equally narrow
        candidates the lower p-value wins.
        """
        reading = self.read(part.metric, tick)
        index = self._index[part.metric]
        target = part.view.amount(part.root).deviation
        if target <= 0:
            return part.root  # the window contradicts the alarm: nothing to narrow it by

        def rank(where: Slice) -> tuple[int, float, float]:
            i = index[where]
            p, s = float(reading.pvalue[i]), float(reading.strength[i])
            return (
                len(where.items),
                -(p if np.isfinite(p) else 1.0),
                abs(s) if np.isfinite(s) else 0.0,
            )

        best = part.root
        for candidate in index:
            if candidate == part.root or not part.root.contains(candidate):
                continue
            # In the incident's direction, and without what pulls the other way.
            deviation = part.view.amount(candidate).deviation
            if deviation <= 0 or deviation < self.policy.explain_share * target:
                continue
            if rank(candidate) > rank(best):
                best = candidate
        return best
