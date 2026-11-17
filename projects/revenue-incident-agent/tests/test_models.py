import pytest

from revwatch.models import Slice


def test_slice_is_order_independent_and_has_a_stable_key() -> None:
    a = Slice.of(platform="app_android", country="DE")
    b = Slice.of(country="DE", platform="app_android")
    assert a == b
    assert a.key == "country=DE|platform=app_android"
    assert Slice().key == "all"
    assert a.as_dict() == {"country": "DE", "platform": "app_android"}


def test_contains_means_same_or_narrower() -> None:
    de = Slice.of(country="DE")
    de_android = Slice.of(country="DE", platform="app_android")
    assert Slice().contains(de)
    assert de.contains(de)
    assert de.contains(de_android)
    assert not de_android.contains(de)
    assert not de.contains(Slice.of(country="FR"))


def test_related_is_ancestor_or_descendant() -> None:
    de = Slice.of(country="DE")
    assert de.related(Slice.of(country="DE", channel="email"))
    assert Slice.of(country="DE", channel="email").related(de)
    assert not de.related(Slice.of(platform="app_ios"))
    assert not de.related(Slice.of(country="FR"))


def test_unknown_dimension_or_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown dimension 'region'"):
        Slice.from_dict({"region": "EU"})
    with pytest.raises(ValueError, match="unknown country value 'XX'"):
        Slice.of(country="XX")


def test_project_keeps_only_the_named_dimensions() -> None:
    de_card = Slice.of(country="DE", payment_method="card")
    assert de_card.project({"country", "platform"}) == Slice.of(country="DE")
    assert de_card.project({"platform"}) == Slice()
    assert de_card.project({"country", "payment_method"}) == de_card
