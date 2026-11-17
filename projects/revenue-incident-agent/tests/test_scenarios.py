from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from revwatch.scenarios import (
    ChangeSpec,
    Effect,
    Label,
    Mitigation,
    Scenario,
    load_labels,
    load_scenarios,
    save_labels,
)

DEV = Path(__file__).resolve().parents[1] / "data" / "scenarios" / "dev.yaml"
SCORING_STARTS = datetime(2026, 7, 27)


def scenario(**overrides: Any) -> Scenario:
    base: dict[str, Any] = {
        "id": "s1",
        "kind": "incident",
        "cause": "unknown",
        "start": datetime(2026, 6, 10, 10),
        "end": datetime(2026, 6, 10, 16),
        "slice": {"country": "DE"},
        "effects": [Effect(param="sessions_mult", value=0.5)],
    }
    return Scenario(**{**base, **overrides})


def test_dev_file_has_the_agreed_counts() -> None:
    scenarios = load_scenarios(DEV)
    labelled = [s for s in scenarios if s.labelled]
    assert sum(s.kind == "incident" for s in labelled) == 14
    assert sum(s.kind == "benign" for s in labelled) == 6
    assert all(s.start >= SCORING_STARTS for s in labelled)
    assert all(s.end <= SCORING_STARTS for s in scenarios if not s.labelled)


def test_dev_file_covers_every_incident_cause() -> None:
    causes = {s.cause for s in load_scenarios(DEV) if s.labelled and s.kind == "incident"}
    assert causes == {
        "payment_provider_failure",
        "release_regression",
        "coupon_misconfiguration",
        "price_error",
        "bot_traffic",
        "tracking_outage",
        "campaign_change",
        "unknown",
    }


def test_dev_file_has_one_injected_instruction() -> None:
    decoys = [d for s in load_scenarios(DEV) for d in s.decoys]
    assert len(decoys) == 1
    assert "AUTOMATED AGENTS" in decoys[0].note


def test_effect_scope_defaults_to_the_scenario_slice() -> None:
    effect = Effect(param="sessions_mult", value=0.5)
    s = scenario(effects=[effect])
    assert s.scope_of(effect) == {"country": "DE"}
    narrowed = Effect(param="sessions_mult", value=0.5, scope={"channel": "email"})
    assert s.scope_of(narrowed) == {"channel": "email"}


def test_effect_that_cannot_use_a_dimension_names_the_scenario() -> None:
    with pytest.raises(
        ValidationError, match=r"s1: sessions_mult cannot be scoped by \['payment_method'\]"
    ):
        scenario(
            slice={"payment_method": "card"}, effects=[Effect(param="sessions_mult", value=0.5)]
        )


def test_end_before_start_names_the_scenario() -> None:
    with pytest.raises(ValidationError, match="s1: end is before start"):
        scenario(end=datetime(2026, 6, 10, 9))


def test_unknown_dimension_value_names_the_scenario() -> None:
    with pytest.raises(ValidationError, match="s1: unknown country value 'XX'"):
        scenario(slice={"country": "XX"})


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        scenario(severity="high")


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    entry = (
        "- {id: a, kind: benign, cause: unknown, start: 2026-06-10T10:00, end: 2026-06-10T12:00}\n"
    )
    path = tmp_path / "dupes.yaml"
    path.write_text(entry + entry)
    with pytest.raises(ValueError, match="duplicate scenario id: a"):
        load_scenarios(path)


def test_file_that_is_not_a_list_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("id: a\n")
    with pytest.raises(ValueError, match="expected a list of scenarios"):
        load_scenarios(path)


def test_labels_round_trip(tmp_path: Path) -> None:
    label = Label(
        scenario_id="s1",
        kind="incident",
        cause="unknown",
        start=datetime(2026, 6, 10, 10),
        end=datetime(2026, 6, 10, 16),
        slice={"country": "DE"},
        labelled=True,
        change_id=None,
        mitigation=None,
    )
    path = tmp_path / "labels.json"
    save_labels([label], path)
    assert load_labels(path) == [label]
    assert label.label_slice.key == "country=DE"


def test_a_yaml_syntax_error_is_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("- id: a\n  kind: [unclosed\n")
    with pytest.raises(ValueError, match="not valid YAML"):
        load_scenarios(path)


def test_a_typo_in_a_field_value_names_the_scenario(tmp_path: Path) -> None:
    path = tmp_path / "typo.yaml"
    path.write_text(
        "- {id: a, kind: benign, cause: unknwon, start: 2026-06-10T10:00, end: 2026-06-10T12:00}\n"
    )
    with pytest.raises(ValueError, match="scenario a:"):
        load_scenarios(path)


def test_times_with_a_time_zone_are_rejected() -> None:
    with pytest.raises(ValidationError, match="s1: times must not carry a time zone"):
        scenario(start="2026-06-10T10:00Z", end="2026-06-10T16:00Z")


def test_scored_planned_events_differ_from_the_ones_in_the_warm_up() -> None:
    by_id = {s.id: s for s in load_scenarios(DEV)}

    def shape(scenario_id: str) -> tuple[object, ...]:
        s = by_id[scenario_id]
        return (s.start.weekday(), s.start.hour, tuple((e.param, e.value) for e in s.effects))

    assert shape("dev-promo-2")[2] != shape("warm-promo-1")[2]
    assert shape("dev-mail") not in (shape("warm-mail-1"), shape("warm-mail-2"))
    assert shape("dev-mail")[:2] != shape("warm-mail-1")[:2]


def test_one_incident_falls_inside_a_planned_promotion() -> None:
    scenarios = load_scenarios(DEV)
    promos = [
        s for s in scenarios if s.labelled and s.calendar and s.calendar.kind == "planned_promotion"
    ]
    inside = [
        s.id
        for s in scenarios
        if s.labelled
        and s.kind == "incident"
        and any(p.start <= s.start and s.end <= p.end for p in promos)
    ]
    assert inside == ["dev-bot"]


def test_a_scenario_that_covers_no_hours_is_rejected() -> None:
    with pytest.raises(ValidationError, match="s1: covers no hours"):
        scenario(end=datetime(2026, 6, 10, 10))


def test_a_change_scoped_by_an_unknown_dimension_names_the_scenario() -> None:
    change = ChangeSpec(kind="release", entity="web-1", note="n", scope={"region": "EU"})
    with pytest.raises(ValidationError, match="s1: change: unknown dimension 'region'"):
        scenario(change=change)
    with pytest.raises(ValidationError, match="s1: decoy: unknown country value 'XX'"):
        scenario(decoys=[change.model_copy(update={"scope": {"country": "XX"}})])


def test_a_mitigation_must_name_the_target_its_action_needs() -> None:
    ok = Mitigation(action="disable_coupon", target={"code": "SOMMER15"})
    assert scenario(mitigation=ok).mitigation == ok
    with pytest.raises(ValidationError, match="s1: disable_coupon needs target code, got sku"):
        scenario(mitigation=Mitigation(action="disable_coupon", target={"sku": "X-1"}))
    with pytest.raises(ValidationError, match="s1: mitigation target: unknown country value 'XX'"):
        scenario(
            mitigation=Mitigation(
                action="enable_bot_challenge", target={"country": "XX", "channel": "affiliate"}
            )
        )


def test_every_mitigation_in_the_dev_file_is_well_formed() -> None:
    assert sum(s.mitigation is not None for s in load_scenarios(DEV)) >= 10


def test_times_must_be_on_the_hour() -> None:
    # The world is hourly. Half past ten would be injected from ten, and labelled half past.
    with pytest.raises(ValidationError, match="s1: start and end must be on the hour"):
        scenario(start=datetime(2026, 6, 10, 10, 30))
    with pytest.raises(ValidationError, match="s1: start and end must be on the hour"):
        scenario(end=datetime(2026, 6, 10, 16, 45))


def test_an_incident_must_change_something() -> None:
    with pytest.raises(ValidationError, match="s1: an incident needs at least one effect"):
        scenario(effects=[])
    assert scenario(kind="benign", cause="expected_event", effects=[]).effects == []


def test_a_ramp_cannot_be_negative() -> None:
    with pytest.raises(ValidationError, match="s1: ramp_hours cannot be negative"):
        scenario(ramp_hours=-2)


def test_late_data_is_a_whole_table_and_takes_no_slice() -> None:
    with pytest.raises(ValidationError, match="s1: drop_table drops the whole table"):
        scenario(kind="benign", cause="expected_event", effects=[], drop_table="traffic")
    late = scenario(
        kind="benign", cause="expected_event", effects=[], slice={}, drop_table="traffic"
    )
    assert late.drop_table == "traffic"


def test_a_scenario_knows_which_watched_metrics_it_moves_and_which_way() -> None:
    outage = scenario(
        slice={"country": "DE", "payment_method": "card"},
        effects=[Effect(param="payment_success_mult", value=0.4)],
    )
    assert outage.moves() == {"payment_success_rate": -1, "checkout_conversion": -1}
    bots = scenario(effects=[Effect(param="observed_sessions_mult", value=3.5)])
    assert bots.moves() == {"sessions": 1, "checkout_conversion": -1}
    coupon = scenario(
        effects=[
            Effect(param="discount_rate_add", value=0.2),
            Effect(param="orders_mult", value=1.4),
        ]
    )
    assert coupon.moves() == {"discount_rate": 1, "gross_margin_rate": -1, "checkout_conversion": 1}
    # Two effects that pull one metric in opposite directions: either direction counts.
    mixed = scenario(
        effects=[
            Effect(param="checkout_rate_mult", value=0.5),
            Effect(param="orders_mult", value=1.4),
        ]
    )
    assert mixed.moves() == {"checkout_conversion": 0}


HELDOUT = DEV.with_name("heldout.yaml")


def test_heldout_file_has_the_agreed_counts_and_only_scores_after_the_warm_up() -> None:
    scenarios = load_scenarios(HELDOUT)
    labelled = [s for s in scenarios if s.labelled]
    assert sum(s.kind == "incident" for s in labelled) == 7
    assert sum(s.kind == "benign" for s in labelled) == 3
    assert all(s.start >= SCORING_STARTS for s in labelled)
    assert all(s.end <= SCORING_STARTS for s in scenarios if not s.labelled)
    assert not {s.id for s in scenarios} & {s.id for s in load_scenarios(DEV) if s.labelled}


def test_heldout_file_has_what_the_dev_file_does_not() -> None:
    scenarios = {s.id: s for s in load_scenarios(HELDOUT)}
    # A provider failure that no status entry announces, with a mitigation all the same.
    silent = scenarios["ho-pay-nostatus"]
    assert silent.change is None and silent.mitigation is not None
    decoys = [d for s in scenarios.values() for d in s.decoys]
    assert len(decoys) == 1 and "AI ASSISTANT" in decoys[0].note
    dev_slices = {
        tuple(sorted(s.slice.items()))
        for s in load_scenarios(DEV)
        if s.labelled and s.kind == "incident"
    }
    held = [s for s in scenarios.values() if s.labelled and s.kind == "incident"]
    assert not any(tuple(sorted(s.slice.items())) in dev_slices for s in held)
