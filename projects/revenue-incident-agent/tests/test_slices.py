from revwatch.models import Slice
from revwatch.slices import enumerate_slices


def test_counts_match_the_spec() -> None:
    assert len(enumerate_slices(("country", "platform", "channel"))) == 90
    assert len(enumerate_slices(("country", "platform", "payment_method"))) == 90
    assert len(enumerate_slices(("country", "platform", "channel", "category"))) == 218


def test_order_is_overall_then_singles_then_pairs() -> None:
    slices = enumerate_slices(("country", "platform"))
    assert slices[0] == Slice()
    assert slices[1] == Slice.of(country="DE")
    assert [len(s.items) for s in slices] == [0] + [1] * 10 + [2] * 24
    assert len(set(slices)) == len(slices)
