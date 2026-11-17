import numpy as np

from revwatch.incidents import Grouper, Reading
from revwatch.models import Alarm, Slice
from revwatch.policy import DetectionPolicy

ALL = Slice()
DE = Slice.of(country="DE")
FR = Slice.of(country="FR")
ANDROID = Slice.of(platform="app_android")
EMAIL = Slice.of(channel="email")
DE_ANDROID = Slice.of(country="DE", platform="app_android")
FR_ANDROID = Slice.of(country="FR", platform="app_android")
DE_EMAIL = Slice.of(country="DE", channel="email")
CARD = Slice.of(payment_method="card")
DE_CARD = Slice.of(country="DE", payment_method="card")

# Expected numerator of each slice in one window: Germany is 40% of everything, and so on.
EXPECTED = {
    ALL: 1000.0,
    DE: 400.0,
    FR: 300.0,
    ANDROID: 500.0,
    EMAIL: 200.0,
    DE_ANDROID: 200.0,
    FR_ANDROID: 150.0,
    DE_EMAIL: 100.0,
    CARD: 500.0,
    DE_CARD: 200.0,
}
FUNNEL = (ALL, DE, FR, ANDROID, EMAIL, DE_ANDROID, FR_ANDROID, DE_EMAIL)
SLICES = {"m1": FUNNEL, "m2": FUNNEL, "pay": (ALL, DE, CARD, DE_CARD)}


class Evidence:
    """What the grouper reads: how far each slice is from expected, as a share of expected.

    A strength of 1 is the detector's own alarm level; here that is a move of 10%.
    """

    def __init__(self) -> None:
        self.move: dict[tuple[str, Slice], float] = {}
        self.strength: dict[tuple[str, Slice], float] = {}
        self.pvalue: dict[tuple[str, Slice], float] = {}

    def set(self, metric: str, moves: dict[Slice, float]) -> None:
        """Replace the moves of `metric`; slices not named are at their expected value."""
        self.move = {key: value for key, value in self.move.items() if key[0] != metric}
        self.move.update({(metric, where): value for where, value in moves.items()})

    def read(self, metric: str, tick: int) -> Reading:
        slices = SLICES[metric]
        move = np.array([self.move.get((metric, s), 0.0) for s in slices])
        expected = np.array([EXPECTED[s] for s in slices])
        return Reading(
            deviation=move * expected,
            expected=expected,
            volume=None,
            strength=np.array(
                [
                    self.strength.get((metric, s), 10.0 * m)
                    for s, m in zip(slices, move, strict=True)
                ]
            ),
            pvalue=np.array(
                [
                    self.pvalue.get((metric, s), 0.001 if m else 1.0)
                    for s, m in zip(slices, move, strict=True)
                ]
            ),
        )

    def alarms(self, tick: int, metric: str, *where: Slice) -> list[Alarm]:
        """Alarms at `tick` for the named slices of `metric`, consistent with the readings."""
        reading = self.read(metric, tick)
        out = []
        for one in where:
            i = SLICES[metric].index(one)
            deviation = float(reading.deviation[i])
            out.append(
                Alarm(
                    metric,
                    one,
                    tick,
                    1 if deviation > 0 else -1,
                    float(reading.strength[i]),
                    deviation,
                )
            )
        return out


def grouper(evidence: Evidence, world_hash: str = "w1") -> Grouper:
    return Grouper(DetectionPolicy(), world_hash, SLICES, evidence.read)


def names(g: Grouper) -> list[tuple[str, str]]:
    return sorted((i.metric, i.slice.key) for i in g.incidents)


# One card outage on Android in Germany: the broader slices fall only because it does.
OUTAGE = {DE_ANDROID: -0.6, DE: -0.3, ANDROID: -0.24, ALL: -0.12}
# A promotion: every slice is 30% above expected.
PROMOTION = dict.fromkeys(FUNNEL, 0.3)
# The promotion, with France on Android at 4.3 times expected on top of it.
PROMOTION_AND_BOTS = {**PROMOTION, FR_ANDROID: 3.3, FR: 1.8, ANDROID: 1.2, ALL: 0.75}


def test_a_drop_in_one_slice_and_the_broader_slices_it_drags_down_are_one_incident() -> None:
    e = Evidence()
    e.set("m1", OUTAGE)
    g = grouper(e)
    opened = g.step(10, e.alarms(10, "m1", ALL, DE, ANDROID, DE_ANDROID))
    assert len(opened) == 1 and len(g.incidents) == 1
    incident = g.incidents[0]
    assert (incident.metric, incident.slice, incident.direction) == ("m1", DE_ANDROID, -1)
    assert incident.members == {("m1", s, -1) for s in (ALL, DE, ANDROID, DE_ANDROID)}
    assert incident.roots == {("m1", DE_ANDROID, -1)}
    assert incident.opened_tick == 10 and incident.closed_tick is None


def test_slices_that_move_as_much_as_the_whole_belong_to_the_whole() -> None:
    e = Evidence()
    e.set("m1", PROMOTION)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", *FUNNEL))
    assert names(g) == [("m1", "all")]
    assert len(g.incidents[0].members) == len(FUNNEL)


def test_a_slice_that_moves_far_more_than_the_rest_is_a_second_incident() -> None:
    e = Evidence()
    e.set("m1", PROMOTION)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", *FUNNEL))
    e.set("m1", PROMOTION_AND_BOTS)
    opened = g.step(11, e.alarms(11, "m1", *FUNNEL))
    assert [(i.metric, i.slice) for i in opened] == [("m1", FR_ANDROID)]
    promotion, bots = g.incidents
    assert promotion.merged_into is None and bots.merged_into is None
    assert promotion.last_alarm_tick == 11  # the promotion's own alert goes on
    # France and Android as a whole are up because of the bots, so they are named with them.
    assert {("m1", FR, 1), ("m1", ANDROID, 1)} <= bots.members
    assert ("m1", DE, 1) not in bots.members


def test_a_weak_alarm_inside_an_alarming_slice_is_not_split_off() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.3, DE_ANDROID: -0.45, ANDROID: -0.18, ALL: -0.12})
    e.strength[("m1", DE_ANDROID)] = -1.5  # a small, noisy slice: barely over its alarm level
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE, DE_ANDROID))
    assert names(g) == [("m1", "country=DE")]


def test_the_same_slice_on_two_metrics_is_one_incident() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5})
    e.set("m2", {DE: 0.5})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE) + e.alarms(10, "m2", DE))
    assert len(g.incidents) == 1
    assert g.incidents[0].members == {("m1", DE, -1), ("m2", DE, 1)}


def test_metrics_are_joined_on_the_dimensions_they_share() -> None:
    e = Evidence()
    e.set("pay", {DE_CARD: -0.6, DE: -0.3, CARD: -0.24, ALL: -0.12})
    e.set("m1", {DE: -0.3, ALL: -0.12})
    g = grouper(e)
    # Payment method is not a dimension of m1, so on what they share both are "DE".
    g.step(10, e.alarms(10, "pay", DE_CARD, DE) + e.alarms(10, "m1", DE))
    assert len(g.incidents) == 1
    assert ("m1", DE, -1) in g.incidents[0].members


def test_a_slice_carried_by_the_whole_on_one_metric_does_not_join_its_incident_on_another() -> None:
    e = Evidence()
    e.set("m2", PROMOTION)  # m2 is up everywhere, Germany on Android included
    e.set("m1", OUTAGE)  # m1 falls in Germany on Android only
    g = grouper(e)
    g.step(10, e.alarms(10, "m2", *FUNNEL) + e.alarms(10, "m1", DE_ANDROID, DE))
    assert names(g) == [("m1", "country=DE|platform=app_android"), ("m2", "all")]
    outage = next(i for i in g.incidents if i.metric == "m1")
    assert all(metric == "m1" for metric, _, _ in outage.members)


def test_the_whole_moving_on_two_metrics_is_one_incident() -> None:
    e = Evidence()
    e.set("m1", PROMOTION)
    e.set("m2", PROMOTION)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", ALL, DE) + e.alarms(10, "m2", ALL, FR))
    assert len(g.incidents) == 1


def test_unrelated_alarms_open_separate_incidents() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5, FR: -0.5})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE, FR))
    assert names(g) == [("m1", "country=DE"), ("m1", "country=FR")]
    assert len({i.incident_id for i in g.incidents}) == 2


def test_the_slice_two_incidents_share_belongs_to_one_of_them() -> None:
    # Germany is down 40% and Android is down 40%, for separate reasons. Germany on
    # Android carries both (0.6 x 0.6 = 0.36 of expected) and must not be reported twice.
    #
    # Known limit: whether the two are reported as two alerts or as one depends on how
    # much of the business each covers. Slices this large (40% and 50% of everything)
    # move the total enough to look like one event; that is accepted and stated in the
    # design. What must hold is that no alarm is lost and none is counted twice.
    e = Evidence()
    e.set("m1", {DE: -0.52, ANDROID: -0.496, DE_ANDROID: -0.64, FR_ANDROID: -0.4, ALL: -0.328})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE, ANDROID, DE_ANDROID, ALL))
    assert 1 <= len(g.incidents) <= 2
    for where in (DE, ANDROID, DE_ANDROID, ALL):
        assert sum(("m1", where, -1) in i.members for i in g.incidents) == 1


def test_a_root_that_alarms_again_before_the_incident_closes_rejoins_it() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    for tick in range(11, 16):
        g.step(tick, [])
    assert g.step(16, e.alarms(16, "m1", DE)) == []  # five quiet hours: still the same incident
    assert len(g.incidents) == 1 and g.incidents[0].last_alarm_tick == 16


def test_an_incident_closes_after_six_quiet_ticks_and_not_before() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    for tick in range(11, 17):
        g.step(tick, [])
    assert g.incidents[0].closed_tick is None  # tick 16 is the sixth quiet tick
    g.step(17, [])
    assert g.incidents[0].closed_tick == 11
    assert len(g.step(18, e.alarms(18, "m1", DE))) == 1  # closed: a new incident
    assert len(g.incidents) == 2


def test_an_incident_with_no_later_ticks_stays_open() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    assert g.incidents[0].closed_tick is None


def test_when_the_root_goes_quiet_a_slice_that_was_just_alarming_continues_the_incident() -> None:
    e = Evidence()
    e.set("m1", OUTAGE)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE_ANDROID, DE))
    # The narrow slice drops out for an hour (too little traffic to score); Germany still alarms.
    e.set("m1", {DE: -0.3, ALL: -0.12})
    assert g.step(11, e.alarms(11, "m1", DE)) == []
    assert len(g.incidents) == 1 and g.incidents[0].last_alarm_tick == 11


def test_a_slice_that_alarmed_in_it_hours_ago_does_not_continue_a_quiet_incident() -> None:
    e = Evidence()
    e.set("m1", PROMOTION)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", *FUNNEL))
    e.set("m1", {})
    for tick in range(11, 15):
        g.step(tick, [])
    # The promotion is over but its alert is still open. France on Android alarms on its own.
    e.set("m1", {FR_ANDROID: 3.0, FR: 1.5, ANDROID: 0.9, ALL: 0.45})
    opened = g.step(15, e.alarms(15, "m1", FR_ANDROID))
    assert [i.slice for i in opened] == [FR_ANDROID]


def test_two_open_incidents_that_turn_out_to_be_one_merge_into_the_older() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    e.set("m2", {DE_ANDROID: -0.5, DE: -0.25, ANDROID: -0.2, ALL: -0.1})
    g.step(11, e.alarms(11, "m1", DE) + e.alarms(11, "m2", DE_ANDROID))
    assert len(g.incidents) == 2
    older, younger = g.incidents
    # An hour later m2 is down in all of Germany: the same place as the first incident.
    e.set("m2", {DE: -0.5, DE_ANDROID: -0.5, ANDROID: -0.2, ALL: -0.2})
    assert g.step(12, e.alarms(12, "m1", DE) + e.alarms(12, "m2", DE, DE_ANDROID)) == []
    assert older.closed_tick is None and older.merged_into is None
    assert {("m1", DE, -1), ("m2", DE, -1), ("m2", DE_ANDROID, -1)} <= older.members
    # The younger alert was raised, so it stays on record: closed, and pointing at the survivor.
    assert g.incidents == [older, younger]
    assert younger.merged_into == older.incident_id and younger.closed_tick == 12
    # A merged incident takes no further alarms.
    g.step(13, e.alarms(13, "m2", DE_ANDROID, DE))
    assert younger.last_alarm_tick == 11 and older.last_alarm_tick == 13


def test_an_overall_alarm_joins_the_slice_that_explains_it_and_does_not_bridge_two() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5, FR: -0.3, ALL: -0.29})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", ALL, DE, FR))
    assert names(g) == [("m1", "country=DE"), ("m1", "country=FR")]
    germany = next(i for i in g.incidents if i.slice == DE)
    assert ("m1", ALL, -1) in germany.members  # Germany is the larger part of the total


def test_an_overall_alarm_alone_rejoins_the_open_incident_that_explains_it() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5, ALL: -0.2})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    # Next hour only the total passes the bar; Germany is still down.
    assert g.step(11, e.alarms(11, "m1", ALL)) == []
    assert len(g.incidents) == 1 and ("m1", ALL, -1) in g.incidents[0].members


def test_the_name_is_refined_to_a_narrower_slice_that_explains_the_root() -> None:
    # Germany on Android did not alarm itself (too small to be scored), but it is
    # where nearly all of Germany's drop is.
    e = Evidence()
    e.set("m1", {DE: -0.3, DE_ANDROID: -0.55})  # -120 and -110
    e.strength[("m1", DE_ANDROID)] = 0.0
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    assert g.incidents[0].slice == DE_ANDROID
    assert g.incidents[0].roots == {("m1", DE, -1)}


def test_between_equally_narrow_names_the_lower_p_value_wins() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.3, DE_ANDROID: -0.55, DE_EMAIL: -1.0})  # -120, -110, -100
    e.strength[("m1", DE_ANDROID)] = 0.0
    e.strength[("m1", DE_EMAIL)] = 0.0
    e.pvalue[("m1", DE_ANDROID)] = 1e-3
    e.pvalue[("m1", DE_EMAIL)] = 1e-6
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    assert g.incidents[0].slice == DE_EMAIL


def test_a_narrower_slice_that_explains_too_little_is_not_the_name() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.3, DE_ANDROID: -0.3})  # -120 and -60: half of it
    e.strength[("m1", DE_ANDROID)] = 0.0
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    assert g.incidents[0].slice == DE


def test_a_slice_moving_the_other_way_is_never_the_name() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.3, DE_ANDROID: 0.6})
    e.strength[("m1", DE_ANDROID)] = 0.0
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE))
    assert g.incidents[0].slice == DE


def test_incident_ids_are_deterministic_and_depend_on_the_world() -> None:
    ids = []
    for world_hash in ("w1", "w1", "w2"):
        e = Evidence()
        e.set("m1", {DE: -0.5})
        g = grouper(e, world_hash)
        g.step(10, e.alarms(10, "m1", DE))
        ids.append(g.incidents[0].incident_id)
    assert ids[0] == ids[1] != ids[2]
    assert ids[0].startswith("INC-")


def test_two_incidents_named_alike_in_the_same_tick_get_different_ids() -> None:
    # Germany and Android both alarm; the slice they share is too small to be scored
    # but holds nearly all of both drops, so both incidents are named after it.
    e = Evidence()
    e.set("m1", {DE: -0.3, ANDROID: -0.24, DE_ANDROID: -0.55})
    e.strength[("m1", DE_ANDROID)] = 0.0
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE, ANDROID))
    assert [i.slice for i in g.incidents] == [DE_ANDROID, DE_ANDROID]
    assert len({i.incident_id for i in g.incidents}) == 2


def test_a_slice_pulling_the_other_way_is_taken_out_before_the_rest_is_judged() -> None:
    # A promotion lifts every slice by 30%. In France on Android something breaks at the
    # same time and the metric falls by 80% there, which holds the total back to +13.5%.
    e = Evidence()
    e.set("m1", {**PROMOTION, FR_ANDROID: -0.8, FR: -0.25, ANDROID: -0.03, ALL: 0.135})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", ALL, DE, EMAIL, DE_ANDROID, DE_EMAIL, FR_ANDROID, FR))
    # Germany is up 30% against a total that is up 13.5%, but only because of France.
    assert names(g) == [("m1", "all"), ("m1", "country=FR|platform=app_android")]
    promotion = next(i for i in g.incidents if i.slice == ALL)
    assert {("m1", DE, 1), ("m1", EMAIL, 1)} <= promotion.members


def test_an_alert_about_the_whole_goes_on_when_the_whole_splits_into_several_roots() -> None:
    e = Evidence()
    e.set("m1", PROMOTION)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", *FUNNEL))
    # The lift turns uneven: Germany and France far above the rest, which is nearly flat.
    # Together they account for the total; neither does alone.
    e.set("m1", {DE: 0.9, FR: 0.5, ALL: 0.525})
    assert g.step(11, e.alarms(11, "m1", ALL, DE, FR)) == []
    assert len(g.incidents) == 1
    assert g.incidents[0].roots == {("m1", DE, 1), ("m1", FR, 1)}
    # And back to an even lift: still the same alert.
    e.set("m1", PROMOTION)
    assert g.step(12, e.alarms(12, "m1", *FUNNEL)) == []
    assert len(g.incidents) == 1


def test_roots_that_explain_the_total_together_stay_apart_without_an_alert_about_it() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.9, FR: -0.5, ALL: -0.525})
    g = grouper(e)
    for tick in (10, 11):
        g.step(tick, e.alarms(tick, "m1", ALL, DE, FR))
    assert names(g) == [("m1", "country=DE"), ("m1", "country=FR")]
    assert all(i.merged_into is None for i in g.incidents)


# An error in German email: Germany and email fall with it, and Germany on Android
# carries its share of Germany's fall.
EMAIL_ERROR = {DE_EMAIL: -0.8, DE: -0.2, EMAIL: -0.4, DE_ANDROID: -0.2, ALL: -0.08}


def test_a_weak_slice_stays_with_its_alert_when_the_slice_that_carried_it_drops_out() -> None:
    e = Evidence()
    e.set("m1", EMAIL_ERROR)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE_EMAIL, DE, EMAIL, DE_ANDROID))
    assert len(g.incidents) == 1 and g.incidents[0].roots == {("m1", DE_EMAIL, -1)}
    # Germany as a whole is back inside its band, so nothing broader explains Germany on
    # Android any more. It is barely out of its own band and was in this alert an hour ago.
    e.strength[("m1", DE)] = -0.5
    e.strength[("m1", DE_ANDROID)] = -1.5
    assert g.step(11, e.alarms(11, "m1", DE_EMAIL, DE_ANDROID)) == []
    assert len(g.incidents) == 1 and g.incidents[0].last_seen[("m1", DE_ANDROID, -1)] == 11


def test_a_strong_slice_leaves_the_alert_it_was_in_when_nothing_explains_it() -> None:
    e = Evidence()
    e.set("m1", EMAIL_ERROR)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE_EMAIL, DE, EMAIL, DE_ANDROID))
    # Germany on Android now falls far out of its band while Germany as a whole does not.
    e.strength[("m1", DE)] = -0.5
    e.strength[("m1", DE_ANDROID)] = -6.0
    opened = g.step(11, e.alarms(11, "m1", DE_EMAIL, DE_ANDROID))
    assert [i.slice for i in opened] == [DE_ANDROID]


# --- found in review: readings where the broader slices are the sums of the narrower ones ---

# Germany down 50% and France on Android down 50%, for separate reasons. France, Android
# and the total fall by what those two take out of them.
TWO_OUTAGES = {DE: -0.5, FR_ANDROID: -0.5, FR: -0.25, ANDROID: -0.15, ALL: -0.275}


def test_two_outages_on_separate_slices_are_two_incidents_when_the_total_alarms() -> None:
    e = Evidence()
    e.set("m1", TWO_OUTAGES)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE, FR_ANDROID, ALL))
    assert names(g) == [("m1", "country=DE"), ("m1", "country=FR|platform=app_android")]


def test_two_outages_on_separate_slices_are_two_incidents_when_the_total_is_only_out_of_band() -> (
    None
):
    e = Evidence()
    e.set("m1", TWO_OUTAGES)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE, FR_ANDROID))
    assert names(g) == [("m1", "country=DE"), ("m1", "country=FR|platform=app_android")]


def test_an_outage_elsewhere_is_raised_while_another_alert_is_open() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5, ALL: -0.2})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE, ALL))
    e.set("m1", TWO_OUTAGES)
    opened = g.step(11, e.alarms(11, "m1", DE, ALL, FR_ANDROID))
    assert [i.slice for i in opened] == [FR_ANDROID]


def test_an_outage_that_dominates_the_total_is_raised_inside_a_mild_promotion() -> None:
    mild = dict.fromkeys(FUNNEL, 0.12)
    e = Evidence()
    e.set("m1", mild)
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", *FUNNEL))
    # France on Android at five times expected: most of the total's move is now this.
    e.set("m1", {**mild, FR_ANDROID: 4.0, FR: 618 / 300, ANDROID: 642 / 500, ALL: 0.702})
    opened = g.step(11, e.alarms(11, "m1", *FUNNEL))
    assert [i.slice for i in opened] == [FR_ANDROID]


def test_an_old_narrow_alert_does_not_claim_a_new_broad_event() -> None:
    e = Evidence()
    e.set("m1", {DE_ANDROID: 0.5, DE: 0.25, ANDROID: 0.2, ALL: 0.1})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE_ANDROID))
    e.set("m1", {})
    for tick in range(11, 15):
        g.step(tick, [])
    # Quiet for four hours and still open. Then everything rises.
    e.set("m1", PROMOTION)
    opened = g.step(15, e.alarms(15, "m1", *FUNNEL))
    assert [i.slice for i in opened] == [ALL]


def test_an_incident_on_two_metrics_goes_on_when_the_root_drops_out_on_one() -> None:
    e = Evidence()
    e.set("m1", OUTAGE)
    e.set("m2", {DE_ANDROID: -0.5, DE: -0.25, ANDROID: -0.2, ALL: -0.1})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE_ANDROID, DE) + e.alarms(10, "m2", DE_ANDROID))
    assert len(g.incidents) == 1
    e.set("m1", {DE: -0.3, ALL: -0.12})
    assert g.step(11, e.alarms(11, "m1", DE) + e.alarms(11, "m2", DE_ANDROID)) == []


def _incidents_after_a_long_run(root_alarmed_once: bool) -> int:
    e = Evidence()
    e.set("m1", {DE_ANDROID: -0.45, DE: -0.45})
    g = grouper(e)
    first = (DE_ANDROID, DE) if root_alarmed_once else (DE_ANDROID,)
    g.step(10, e.alarms(10, "m1", *first))
    for tick in range(11, 20):
        g.step(tick, e.alarms(tick, "m1", DE_ANDROID))
    assert len(g.incidents) == 1 and g.incidents[0].roots == {("m1", DE, -1)}
    e.set("m1", {DE_ANDROID: -0.45, DE: -0.45, DE_EMAIL: -1.0})
    e.strength[("m1", DE_EMAIL)] = -1.5
    g.step(20, e.alarms(20, "m1", DE_ANDROID, DE_EMAIL))
    return len(g.incidents)


def test_a_root_counts_as_recent_while_it_is_the_root_whether_or_not_it_alarms() -> None:
    # Germany explains the alarm on Germany-on-Android every hour, out of band but never
    # passing false-alarm control itself. That must not make it more or less of a root.
    assert _incidents_after_a_long_run(True) == _incidents_after_a_long_run(False) == 1


def test_an_alarming_slice_is_not_handed_to_a_small_root_inside_it() -> None:
    austria, austria_email = Slice.of(country="AT"), Slice.of(country="AT", channel="email")
    # Averaged over the window Austria is just inside its band, but it alarms: -120.
    # The email slice inside it holds 8 of that.
    reading = Reading(
        deviation=np.array([-128.0, -120.0, -8.0]),
        expected=np.array([5000.0, 400.0, 8.0]),
        volume=None,
        strength=np.array([0.0, -0.9, -3.0]),
        pvalue=np.array([1.0, 1e-4, 1e-6]),
    )
    slices = {"m1": (ALL, austria, austria_email)}
    g = Grouper(DetectionPolicy(), "w", slices, lambda metric, tick: reading)
    g.step(
        10,
        [
            Alarm("m1", austria, 10, -1, -1.8, -120.0),
            Alarm("m1", austria_email, 10, -1, -3.0, -8.0),
        ],
    )
    assert any(i.slice == austria for i in g.incidents)


def test_an_alarm_its_own_window_contradicts_is_named_as_it_alarmed_and_joins_nothing() -> None:
    e = Evidence()
    e.set("m1", {DE: 0.05, DE_ANDROID: -0.005, FR: -0.6, ALL: -0.16})
    e.strength[("m1", DE)] = -0.2
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", FR))
    # Germany alarms downwards although over the window it is slightly up.
    opened = g.step(11, e.alarms(11, "m1", FR) + [Alarm("m1", DE, 11, -1, -4.0, 20.0)])
    assert [i.slice for i in opened] == [DE]


# --- behaviours that could be removed without another test noticing -------------------------


def test_a_rate_that_moved_by_the_same_points_as_its_broader_slice_belongs_to_it() -> None:
    # A coupon adds ten points of discount everywhere. Germany runs at 2% and email at
    # 40%, so as a share of expected Germany moved seven times as much as the rest of
    # the business; in points they agree.
    volume = np.array([1000.0, 400.0, 200.0])  # all, Germany, email
    reading = Reading(
        deviation=0.10 * volume,
        expected=np.array([96.0, 8.0, 80.0]),
        volume=volume,
        strength=np.array([5.0, 5.0, 5.0]),
        pvalue=np.array([1e-6, 1e-6, 1e-6]),
    )
    slices = {"rate": (ALL, DE, EMAIL)}
    g = Grouper(DetectionPolicy(), "w", slices, lambda metric, tick: reading)
    g.step(10, [Alarm("rate", s, 10, 1, 5.0, 0.0) for s in (ALL, DE, EMAIL)])
    assert [(i.slice, len(i.members)) for i in g.incidents] == [(ALL, 3)]


def test_roots_with_no_dimension_in_common_are_not_the_same_place() -> None:
    # Card payments fail (payments know country and method), and email sessions fall
    # (the funnel knows country, platform and channel). Seen through what both have,
    # the country, each is "everywhere" - which says nothing about the other.
    e = Evidence()
    e.set("pay", {CARD: -0.5, ALL: -0.25})
    e.set("m1", {EMAIL: -0.5, ALL: -0.1})
    g = grouper(e)
    g.step(10, e.alarms(10, "pay", CARD) + e.alarms(10, "m1", EMAIL))
    assert names(g) == [("m1", "channel=email"), ("pay", "payment_method=card")]


def test_a_broader_slice_that_is_only_out_of_band_must_move_itself_to_carry_an_alarm() -> None:
    e = Evidence()
    # German email is down 15%. Germany reads as out of its band, but all of its move
    # is the email slice.
    e.set("m1", {DE_EMAIL: -0.15, DE: -0.0375})
    e.strength[("m1", DE)] = -1.2
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE_EMAIL))
    assert g.incidents[0].roots == {("m1", DE_EMAIL, -1)}


def test_an_incident_on_two_metrics_is_named_after_its_strongest_alarm() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.3})
    e.set("m2", {DE: -0.6})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE) + e.alarms(10, "m2", DE))
    assert [(i.metric, i.slice) for i in g.incidents] == [("m2", DE)]


def test_a_merged_incident_hands_its_recent_alarms_to_the_survivor() -> None:
    e = Evidence()
    e.set("m1", {DE: -0.5})
    e.set("m2", {DE_ANDROID: -0.5})
    g = grouper(e)
    g.step(10, e.alarms(10, "m1", DE) + e.alarms(10, "m2", DE_ANDROID))
    assert len(g.incidents) == 2
    # On the second metric Germany as a whole follows: one place on both metrics, one incident.
    e.set("m2", {DE_ANDROID: -0.5, DE: -0.5})
    g.step(11, e.alarms(11, "m1", DE) + e.alarms(11, "m2", DE))
    survivor = next(i for i in g.incidents if i.merged_into is None)
    assert survivor.last_seen[("m2", DE_ANDROID, -1)] == 10  # from the incident it took over
    assert {("m1", DE, -1), ("m2", DE, -1), ("m2", DE_ANDROID, -1)} <= survivor.members
