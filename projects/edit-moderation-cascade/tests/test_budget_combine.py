"""The paced daily budget, and the two weights that turn two tiers into one risk."""

from __future__ import annotations

import random

import pytest

from editwatch.budget import DAY_S, PacedBudget
from editwatch.combine import DEFAULT, Combiner, fit, load, save

HOUR = 3600.0

# -- the budget -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "allowance"),
    [(0, 1.0), (6, 7.0), (12, 13.0), (18, 19.0), (23, 24.0), (24, 24.0)],
)
def test_the_allowance_grows_evenly_through_the_day_on_top_of_the_headroom(hour, allowance):
    # A cap of 24 makes the allowance one unit an hour, plus a twenty-fourth as headroom.
    assert PacedBudget(24.0).allowance(hour * HOUR) == pytest.approx(allowance)


def test_the_allowance_never_exceeds_the_cap_or_falls_below_the_headroom():
    budget = PacedBudget(0.50)
    moments = [-HOUR, 0, 1, HOUR, 12 * HOUR, 23.5 * HOUR, DAY_S - 1, DAY_S, 3 * DAY_S]
    allowances = [budget.allowance(t) for t in moments]
    assert allowances == sorted(allowances)
    assert all(budget.headroom_usd <= a <= 0.50 for a in allowances)
    assert allowances[0] == pytest.approx(0.50 / 24)
    assert allowances[-1] == 0.50


def test_the_headroom_can_be_changed():
    assert PacedBudget(24.0, headroom_share=0.0).allowance(0) == 0.0
    assert PacedBudget(24.0, headroom_share=0.5).allowance(0) == 12.0


def test_a_call_is_refused_and_counted_once_spending_reaches_the_allowance():
    budget = PacedBudget(24.0)
    assert budget.allows(0) is True
    budget.add(0.6)
    assert budget.allows(0) is True
    budget.add(0.4)
    assert budget.allows(0) is False
    assert budget.allows(0) is False
    assert budget.refused == 2
    assert budget.spent_usd == pytest.approx(1.0)


def test_a_refused_budget_allows_calls_again_later_in_the_day():
    budget = PacedBudget(24.0, spent_usd=5.0)
    assert budget.allows(2 * HOUR) is False
    assert budget.allows(4 * HOUR) is False  # the allowance is exactly what was spent
    assert budget.allows(4 * HOUR + 1) is True
    assert budget.refused == 2


def test_nothing_is_allowed_at_any_hour_once_the_cap_is_spent():
    budget = PacedBudget(0.50)
    budget.add(0.50)
    assert [budget.allows(t) for t in (0, 12 * HOUR, DAY_S, 2 * DAY_S)] == [False] * 4
    assert budget.refused == 4


# -- the combiner ---------------------------------------------------------------------


def test_the_combined_risk_is_a_probability_even_at_the_extremes():
    for combiner in (DEFAULT, Combiner(intercept=2.0, weight_tier1=-3.0, weight_model=9.0)):
        for tier1 in (0.0, 1e-9, 0.2, 0.999, 1.0):
            for model_risk in (0, 50, 100):
                assert 0.0 <= combiner.risk(tier1, model_risk) <= 1.0
    assert 0.0 < DEFAULT.risk(0.0, 0) < DEFAULT.risk(1.0, 100) < 1.0


def test_the_combined_risk_rises_with_the_models_risk():
    risks = [DEFAULT.risk(0.3, model_risk) for model_risk in range(0, 101, 10)]
    assert risks == sorted(risks) and len(set(risks)) == len(risks)
    assert risks[0] < 0.1 and risks[-1] > 0.5


def test_the_combined_risk_rises_with_the_tier_1_score():
    risks = [DEFAULT.risk(tier1, 50) for tier1 in (0.01, 0.1, 0.3, 0.6, 0.9, 0.99)]
    assert risks == sorted(risks) and len(set(risks)) == len(risks)


def separable(rows: int = 200, seed: int = 3):
    """Edits the model read: it gave the reverted ones a high risk, tier 1 knew nothing."""
    rng = random.Random(seed)
    reverted = [n % 3 == 0 for n in range(rows)]
    model_risk = [rng.randint(60, 100) if was else rng.randint(0, 40) for was in reverted]
    tier1 = [rng.uniform(0.05, 0.6) for _ in reverted]
    return tier1, model_risk, reverted


def test_fitting_finds_a_positive_weight_for_a_model_that_separates_the_outcomes():
    tier1, model_risk, reverted = separable()
    combiner = fit(tier1, model_risk, reverted)
    assert combiner.weight_model > 1.0
    assert combiner.weight_model > abs(combiner.weight_tier1)
    assert (combiner.fitted_on, combiner.reverted) == (200, sum(reverted))
    assert combiner.risk(0.3, 90) > 0.5 > combiner.risk(0.3, 10)


@pytest.mark.parametrize("outcome", [True, False])
def test_fitting_refuses_edits_that_all_ended_the_same_way(outcome):
    tier1, model_risk, _ = separable()
    with pytest.raises(ValueError, match="both outcomes"):
        fit(tier1, model_risk, [outcome] * 200)


def test_fitting_refuses_fewer_than_thirty_edits():
    tier1, model_risk, reverted = separable(29)
    with pytest.raises(ValueError, match="at least 30"):
        fit(tier1, model_risk, reverted)
    assert fit(*separable(30)).fitted_on == 30


def test_saved_weights_load_back_the_same(tmp_path):
    combiners = {
        "scripted-model": fit(*separable()),
        "another-model": Combiner(
            intercept=-2.5, weight_tier1=0.25, weight_model=3.5, fitted_on=40, reverted=9
        ),
    }
    path = tmp_path / "data" / "combiners.json"
    save(combiners, path)
    assert load(path) == combiners
    save({}, path)
    assert load(path) == {}


def test_loading_weights_from_a_file_that_is_not_there_gives_none(tmp_path):
    assert load(tmp_path / "missing.json") == {}
