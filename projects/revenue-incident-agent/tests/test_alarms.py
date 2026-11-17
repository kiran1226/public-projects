import numpy as np

from revwatch.alarms import bh_reject, persistent
from revwatch.policy import DetectionPolicy


def test_bh_rejects_the_textbook_example() -> None:
    p = np.array([0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205, 0.212, 0.216])
    assert bh_reject(p, 0.05).tolist() == [True, True] + [False] * 8


def test_bh_is_a_step_up_procedure() -> None:
    # 0.02 misses its own threshold (0.0167) but is rejected because a later one passes.
    assert bh_reject(np.array([0.03, 0.02, 0.021]), 0.05).tolist() == [True, True, True]


def test_bh_ignores_unscored_entries() -> None:
    p = np.array([np.nan, 0.001, np.nan, 0.9])
    assert bh_reject(p, 0.05).tolist() == [False, True, False, False]
    assert not bh_reject(np.array([np.nan, np.nan]), 0.05).any()
    assert bh_reject(np.array([]), 0.05).shape == (0,)


def test_bh_counts_only_scored_entries_as_tests() -> None:
    # With m = 2 the threshold for the smallest p is 0.025; with m = 4 it would be 0.0125.
    p = np.array([0.02, np.nan, np.nan, 0.9])
    assert bh_reject(p, 0.05).tolist() == [True, False, False, False]


def test_persistence_needs_two_of_three_in_the_same_direction() -> None:
    passed = np.array(
        [
            [0, 1, 0, 1],  # two of the last three: alarms
            [0, 0, 0, 1],  # only now
            [1, 1, 0, 0],  # not passing now
            [0, -1, 1, 1],  # two of the last three are +1: alarms
            [0, 1, 0, -1],  # direction flipped
        ],
        dtype=np.int8,
    )
    assert persistent(passed, 3, need=2, window=3).tolist() == [1, 0, 0, 1, 0]
    assert persistent(passed, 0, need=2, window=3).tolist() == [0, 0, 0, 0, 0]
    assert persistent(-passed, 3, need=2, window=3).tolist() == [-1, 0, 0, -1, 0]


def test_policy_defaults_match_the_spec() -> None:
    policy = DetectionPolicy()
    assert (policy.fdr_q, policy.persistence_need, policy.persistence_window) == (0.05, 2, 3)
    assert (policy.link_gap_hours, policy.close_after_quiet_hours) == (3, 6)
    assert (policy.warmup_hours, policy.fold_hours, policy.explain_share) == (56 * 24, 168, 0.8)
